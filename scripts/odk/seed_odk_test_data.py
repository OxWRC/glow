#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests", "tqdm", "urllib3"]
# requires-python = ">=3.12"
# ///
"""Seed ODK Central from transformed multi-form mock-data outputs.

This script consumes the deterministic per-form CSVs emitted by
`scripts/odk/transform_mock_data.py` and seeds them into ODK Central in the
correct phase order:

1. upload BeWell v1
2. seed BeWell v1 submissions
3. upload BeWell v2
4. seed BeWell v2 submissions
5. upload and seed PHQ-9 submissions
6. upload and seed demographics submissions

The script is idempotent at the submission level: rows whose `instance_id`
already exist for a form are skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from requests.auth import HTTPBasicAuth
from tqdm import tqdm


HELPER_COLUMNS = {
    "instance_id",
    "wave",
    "period_id",
    "form_version",
    "target_created_at",
    "boundary_case",
}


@dataclass(frozen=True)
class SeedPhase:
    name: str
    form_id: str
    csv_name: str
    form_xml_name: str


PHASES = [
    SeedPhase(
        name="BeeWell v1",
        form_id="bewell_questionnaire",
        csv_name="bewell_questionnaire_v1.csv",
        form_xml_name="bewell_questionnaire_v1.xml",
    ),
    SeedPhase(
        name="BeeWell v2",
        form_id="bewell_questionnaire",
        csv_name="bewell_questionnaire_v2.csv",
        form_xml_name="bewell_questionnaire_v2.xml",
    ),
    SeedPhase(
        name="PHQ-9",
        form_id="phq9_questionnaire",
        csv_name="phq9_questionnaire.csv",
        form_xml_name="phq9_questionnaire.xml",
    ),
    SeedPhase(
        name="Demographics",
        form_id="demographics_questionnaire",
        csv_name="demographics_questionnaire.csv",
        form_xml_name="demographics_questionnaire.xml",
    ),
]


def normalize_instance_id(instance_id: str) -> str:
    stripped = instance_id.strip()
    if not stripped:
        return ""
    if stripped.startswith("uuid:"):
        return stripped
    return f"uuid:{stripped}"


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def extract_form_identity(xml_path: Path) -> tuple[str, str]:
    root = ET.parse(xml_path).getroot()
    data = root.find(".//{*}instance/{*}data")
    if data is None:
        raise ValueError(f"No <data> instance found in {xml_path}")
    form_id = data.attrib.get("id", "")
    version = data.attrib.get("version", "")
    if not form_id:
        raise ValueError(f"No xmlFormId found in {xml_path}")
    if not version:
        raise ValueError(f"No form version found in {xml_path}")
    return form_id, version


class ODKSeeder:
    """Seed submissions and upload form definitions to ODK Central."""

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        project_id: int,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(email, password)
        self.project_id = project_id
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = False

        if "localhost" in base_url or "127.0.0.1" in base_url:
            self.session.headers.update({"Host": "odk.local"})

        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def upload_form_xml(self, xml_path: Path) -> tuple[str, str]:
        form_id, version = extract_form_identity(xml_path)
        xml_content = xml_path.read_text(encoding="utf-8")
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms?publish=true"
        headers = {
            "Content-Type": "application/xml",
            "X-XmlFormId-Fallback": "true",
        }

        response = self.session.post(
            url, data=xml_content.encode("utf-8"), headers=headers
        )
        if response.ok:
            return form_id, version

        try:
            payload = response.json()
        except json.JSONDecodeError:
            payload = {}

        if str(payload.get("code")) == "409.3":
            current = self.get_form_metadata(form_id)
            if str(current.get("version", "")) == str(version):
                return form_id, version

            versions = {
                str(item.get("version", "")) for item in self.get_form_versions(form_id)
            }
            if str(version) in versions:
                return form_id, version

            draft_upload_url = (
                f"{self.base_url}/v1/projects/{self.project_id}/forms/"
                f"{form_id}/draft?ignoreWarnings=true"
            )
            draft_response = self.session.post(
                draft_upload_url,
                data=xml_content.encode("utf-8"),
                headers=headers,
            )
            draft_response.raise_for_status()

            publish_url = (
                f"{self.base_url}/v1/projects/{self.project_id}/forms/"
                f"{form_id}/draft/publish?ignoreWarnings=true"
            )
            publish_response = self.session.post(publish_url)
            if publish_response.ok:
                return form_id, version
            publish_response.raise_for_status()

        response.raise_for_status()
        raise RuntimeError(
            f"Unexpected upload response for {xml_path}: {response.text}"
        )

    def get_form_metadata(self, form_id: str) -> dict:
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def get_form_versions(self, form_id: str) -> list[dict]:
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}/versions"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def get_existing_submissions(self, form_id: str) -> set[str]:
        url = (
            f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}/submissions"
        )
        response = self.session.get(url)
        if response.status_code == 404:
            raise RuntimeError(
                f"Form '{form_id}' not found in project {self.project_id}; upload forms first."
            )
        response.raise_for_status()
        submissions = response.json()
        instance_ids = {
            normalize_instance_id(sub.get("instanceId", "")) for sub in submissions
        }
        return instance_ids - {""}

    def create_submission_xml(
        self,
        row: dict[str, str],
        form_id: str,
        form_version: str,
        instance_id: str,
    ) -> str:
        data = ET.Element(
            "data",
            attrib={
                "id": form_id,
                "version": form_version,
            },
        )

        meta = ET.SubElement(data, "meta")
        instance_element = ET.SubElement(meta, "instanceID")
        instance_element.text = instance_id

        for field_name, value in row.items():
            clean_name = field_name.strip().strip("\ufeff")
            if clean_name in HELPER_COLUMNS or clean_name == "meta":
                continue
            if not clean_name or value == "":
                continue
            element = ET.SubElement(data, clean_name)
            element.text = str(value).strip()

        xml_str = ET.tostring(data, encoding="unicode", method="xml")
        return f'<?xml version="1.0"?>\n{xml_str}'

    def submit_submission(self, form_id: str, xml_data: str, instance_id: str) -> bool:
        url = (
            f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}/submissions"
        )
        headers = {"Content-Type": "application/xml"}
        response = self.session.post(
            url, data=xml_data.encode("utf-8"), headers=headers
        )
        if response.ok:
            return True
        if response.status_code == 409:
            return False

        print(
            f"\n❌ Failed to submit {instance_id} to {form_id}: {response.status_code}"
        )
        print(f"   Response: {response.text}")
        return False

    def seed_phase(
        self,
        csv_path: Path,
        form_id: str,
        form_version: str,
        limit: int | None = None,
    ) -> dict[str, int]:
        rows = read_csv_rows(csv_path)
        total_rows = len(rows)
        if limit is not None:
            rows = rows[:limit]

        existing_ids = self.get_existing_submissions(form_id)

        new_rows: list[dict[str, str]] = []
        for row in rows:
            instance_id = normalize_instance_id(row.get("instance_id", ""))
            if not instance_id:
                raise ValueError(
                    f"Row in {csv_path} is missing instance_id helper column"
                )
            if instance_id not in existing_ids:
                new_rows.append(row)

        stats = {
            "total": len(rows),
            "source_total": total_rows,
            "existing": len(rows) - len(new_rows),
            "submitted": 0,
            "failed": 0,
        }

        if not new_rows:
            return stats

        for row in tqdm(
            new_rows, desc=f"Submitting {csv_path.name}", unit="submission"
        ):
            instance_id = normalize_instance_id(row["instance_id"])
            row_version = row.get("form_version", form_version) or form_version
            xml_data = self.create_submission_xml(
                row, form_id, row_version, instance_id
            )
            success = self.submit_submission(form_id, xml_data, instance_id)
            if success:
                stats["submitted"] += 1
            else:
                stats["failed"] += 1
            time.sleep(0.05)

        return stats


def manifest_counts(manifest_rows: Iterable[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in manifest_rows:
        csv_file = row.get("csv_file", "")
        counts[csv_file] = counts.get(csv_file, 0) + 1
    return counts


def print_phase_summary(phase: SeedPhase, stats: dict[str, int]) -> None:
    print(f"\n{phase.name} ({phase.csv_name})")
    print(f"   Rows in phase:      {stats['total']}")
    print(f"   Already existed:    {stats['existing']}")
    print(f"   Newly submitted:    {stats['submitted']}")
    print(f"   Failed:             {stats['failed']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed transformed multi-form mock data into ODK Central",
    )
    parser.add_argument(
        "--seed-dir",
        type=Path,
        required=True,
        help="Directory containing transformed per-form CSVs",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional manifest.csv path (defaults to <seed-dir>/manifest.csv)",
    )
    parser.add_argument(
        "--forms-dir",
        type=Path,
        default=Path("odk-forms"),
        help="Directory containing form XMLs",
    )
    parser.add_argument(
        "--odk-url", type=str, required=True, help="ODK Central base URL"
    )
    parser.add_argument(
        "--email", type=str, required=True, help="ODK Central user email"
    )
    parser.add_argument(
        "--password", type=str, required=True, help="ODK Central user password"
    )
    parser.add_argument(
        "--project-id", type=int, required=True, help="ODK Central project ID"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional per-phase row limit for testing",
    )
    parser.add_argument(
        "--skip-form-upload",
        action="store_true",
        help="Skip uploading form XMLs before seeding",
    )
    args = parser.parse_args()

    manifest_path = args.manifest or (args.seed_dir / "manifest.csv")
    if not manifest_path.exists():
        print(f"❌ Manifest file not found: {manifest_path}")
        sys.exit(1)

    manifest_rows = read_csv_rows(manifest_path)
    expected_counts = manifest_counts(manifest_rows)

    for phase in PHASES:
        csv_path = args.seed_dir / phase.csv_name
        xml_path = args.forms_dir / phase.form_xml_name
        if not csv_path.exists():
            print(f"❌ Seed CSV not found: {csv_path}")
            sys.exit(1)
        if not xml_path.exists():
            print(f"❌ Form XML not found: {xml_path}")
            sys.exit(1)

        actual_count = len(read_csv_rows(csv_path))
        expected_count = expected_counts.get(phase.csv_name, 0)
        if actual_count != expected_count:
            print(
                f"❌ Count mismatch for {phase.csv_name}: "
                f"csv has {actual_count} rows but manifest expects {expected_count}"
            )
            sys.exit(1)

    seeder = ODKSeeder(
        base_url=args.odk_url,
        email=args.email,
        password=args.password,
        project_id=args.project_id,
    )

    overall = {
        "total": 0,
        "existing": 0,
        "submitted": 0,
        "failed": 0,
    }

    started = datetime.now()

    for phase in PHASES:
        csv_path = args.seed_dir / phase.csv_name
        xml_path = args.forms_dir / phase.form_xml_name

        print(f"\n== {phase.name} ==")
        if not args.skip_form_upload:
            form_id, form_version = seeder.upload_form_xml(xml_path)
        else:
            form_id, form_version = extract_form_identity(xml_path)

        if form_id != phase.form_id:
            print(
                f"❌ Expected form_id {phase.form_id} but {xml_path} defines {form_id}"
            )
            sys.exit(1)

        stats = seeder.seed_phase(csv_path, form_id, form_version, limit=args.limit)
        print_phase_summary(phase, stats)

        for key in overall:
            overall[key] += stats[key]

        if stats["failed"] > 0:
            print(f"\n⚠️  Phase '{phase.name}' had failed submissions; stopping")
            sys.exit(1)

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n{'=' * 60}")
    print("✅ Multi-form seeding complete")
    print(f"{'=' * 60}")
    print(f"   Total rows seen:     {overall['total']}")
    print(f"   Already existed:     {overall['existing']}")
    print(f"   Newly submitted:     {overall['submitted']}")
    print(f"   Failed:              {overall['failed']}")
    print(f"   Time elapsed:        {elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
