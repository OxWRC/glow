#!/usr/bin/env python3
"""Rewrite seeded ODK submission timestamps from a manifest.

This script updates the timestamps used by ODK's submission exports so seeded
demo/dev data land in the intended academic-year buckets.

It updates:

- `submissions.createdAt`
- `submissions.updatedAt` when it is currently null
- `submission_defs.createdAt`

Rows are matched by `instance_id` from `manifest.csv`.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_manifest_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_copy_payload(rows: list[dict[str, str]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["instance_id", "target_created_at"])
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "instance_id": row["instance_id"],
                "target_created_at": row["target_created_at"],
            }
        )
    return buffer.getvalue()


def build_sql(copy_payload: str, dry_run: bool) -> str:
    statements = [
        "BEGIN;",
        "CREATE TEMP TABLE timestamp_updates (instance_id varchar(64), target_created_at timestamptz);",
        "COPY timestamp_updates (instance_id, target_created_at) FROM STDIN WITH (FORMAT csv, HEADER true);",
        copy_payload.rstrip("\n"),
        "\\.",
        "SELECT count(*) AS manifest_rows FROM timestamp_updates;",
        'SELECT count(*) AS matched_submissions FROM submissions s JOIN timestamp_updates tu ON s."instanceId" = tu.instance_id;',
        'SELECT count(*) AS matched_submission_defs FROM submission_defs sd JOIN timestamp_updates tu ON sd."instanceId" = tu.instance_id;',
    ]

    if dry_run:
        statements.append("ROLLBACK;")
    else:
        statements.extend(
            [
                'UPDATE submissions s SET "createdAt" = tu.target_created_at, "updatedAt" = COALESCE(s."updatedAt", tu.target_created_at) FROM timestamp_updates tu WHERE s."instanceId" = tu.instance_id;',
                'UPDATE submission_defs sd SET "createdAt" = tu.target_created_at FROM timestamp_updates tu WHERE sd."instanceId" = tu.instance_id;',
                "COMMIT;",
            ]
        )

    return "\n".join(statements) + "\n"


def run_psql(
    sql_text: str, db_service: str, db_user: str, db_name: str
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        db_service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        db_user,
        "-d",
        db_name,
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=sql_text,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rewrite ODK submission createdAt timestamps from manifest.csv"
    )
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Manifest CSV path"
    )
    parser.add_argument(
        "--db-service",
        default="postgres14",
        help="Docker compose service name for ODK Postgres",
    )
    parser.add_argument("--db-user", default="odk", help="ODK Postgres username")
    parser.add_argument("--db-name", default="odk", help="ODK Postgres database name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate matching rows without applying updates",
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"❌ Manifest not found: {args.manifest}")
        sys.exit(1)

    manifest_rows = read_manifest_rows(args.manifest)
    if not manifest_rows:
        print(f"❌ Manifest is empty: {args.manifest}")
        sys.exit(1)

    missing = [
        row
        for row in manifest_rows
        if not row.get("instance_id") or not row.get("target_created_at")
    ]
    if missing:
        print(
            f"❌ Manifest rows missing instance_id or target_created_at: {len(missing)}"
        )
        sys.exit(1)

    sql_text = build_sql(build_copy_payload(manifest_rows), args.dry_run)
    result = run_psql(sql_text, args.db_service, args.db_user, args.db_name)

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    print(result.stdout)
    if args.dry_run:
        print("✅ Dry-run timestamp verification complete")
    else:
        print("✅ Submission timestamps rewritten")


if __name__ == "__main__":
    main()
