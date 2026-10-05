"""ODK Central client for fetching multi-form submissions and metadata."""

import logging
import os
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Dict, Optional
import re
import hashlib
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import urllib3

logger = logging.getLogger(__name__)

# Disable SSL warnings if SSL verification is disabled
if os.getenv("GLOW_ODK_VERIFY_SSL", "true").lower() == "false":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ODKClient:
    """Client for interacting with ODK Central."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        project_id: int,
        verify_ssl: bool = True,
        host_header: str = "",
    ):
        """Initialize ODK client.

        Args:
            base_url: ODK Central base URL (e.g., "http://service:8383")
            username: ODK Central username/email
            password: ODK Central password
            project_id: Default project ID
            verify_ssl: Whether to verify SSL certificates (default: True)
            host_header: Host header to send, when the URL's host isn't the
                name ODK's nginx serves (e.g. "odk.local" via https://nginx)
        """
        self.access_token = None
        self.token_expires = None
        self.base_url = base_url
        self.username = username
        self.password = password
        self.project_id = project_id
        self.verify_ssl = verify_ssl

        # ODK's nginx routes by server_name; anything else hits its 421 catch-all.
        self.default_headers = {"Host": host_header} if host_header else {}

        self._try_login()

    def _try_login(self) -> bool:
        if self.token_expires is not None and self.token_expires > datetime.now(
            timezone.utc
        ):
            return True
        # Authenticate with ODK Central
        try:
            response = requests.post(
                f"{self.base_url}/v1/sessions",
                json={"email": self.username, "password": self.password},
                headers=self.default_headers,
                verify=self.verify_ssl,
                timeout=10,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as err:
            logger.warning(f"ODKClient.try_login failed {err}")
            return False
        try:
            data = response.json()
            self.access_token = data["token"]
            self.token_expires = datetime.fromisoformat(
                data["expiresAt"].replace("Z", "+00:00")
            )
        except (JSONDecodeError, KeyError):
            logger.warning(f"Unexpeted ODK Central response format: {response.content}")
            return False
        return True

    def _require_login(self) -> None:
        # Raise rather than return an empty result: callers can't tell "no
        # submissions" from "couldn't log in", and DataStore only keeps its
        # previous data (and cache) when the fetch raises.
        if not self._try_login():
            raise RuntimeError("ODK Central login failed")

    def get(self, *args, **kwargs) -> requests.Response:
        # Add SSL verification setting if not explicitly provided
        if "verify" not in kwargs:
            kwargs["verify"] = self.verify_ssl

        # Merge default headers with provided headers
        headers = dict(self.default_headers)
        if "headers" in kwargs:
            headers.update(kwargs["headers"])
        kwargs["headers"] = headers
        kwargs.setdefault("timeout", 30)

        request = requests.get(*args, auth=(self.username, self.password), **kwargs)
        request.raise_for_status()
        return request

    def _list_forms(self) -> list[dict[str, Any]]:
        """List forms visible in the configured project."""
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms"
        response = self.get(url)
        return response.json()

    def _fetch_form_odata(
        self,
        form_id: str,
        etag: Optional[str] = None,
    ) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
        """Fetch all submissions for one form via OData, following pagination."""
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}.svc/Submissions"
        response = self.get(url, headers={"If-None-Match": etag} if etag else {})
        if response.status_code == 304:
            return None, etag
        payload = response.json()
        new_etag = response.headers.get("etag")

        records: list[dict[str, Any]] = payload.get("value", [])
        while payload.get("@odata.nextLink"):
            response = self.get(payload["@odata.nextLink"])
            payload = response.json()
            records.extend(payload.get("value", []))
        return records, new_etag

    def _build_form_submissions_dataframe(
        self,
        form_id: str,
        records: list[dict[str, Any]],
    ) -> pd.DataFrame:
        """Build a DataFrame from OData submission records, flattening groups."""
        if not records:
            return pd.DataFrame(
                columns=[
                    "__xmlFormId",
                    "__version",
                    "instanceId",
                    "createdAt",
                    "updatedAt",
                    "SubmissionDate",
                ]
            )
        df = pd.json_normalize(records, sep="/")
        df["__xmlFormId"] = form_id
        df["__version"] = df.get("__system/formVersion")
        df["instanceId"] = df["__id"]
        df["createdAt"] = df["__system/submissionDate"]
        df["updatedAt"] = df.get("__system/updatedAt")
        df["SubmissionDate"] = df["createdAt"]
        return df

    def fetch_form_submissions(
        self,
        form_id: str,
        etag: Optional[str] = None,
    ) -> tuple[Optional[pd.DataFrame], Optional[str]]:
        """Fetch submissions for one form, respecting response ETags."""
        logger.info(
            "Fetching submissions for project=%d, form=%s",
            self.project_id,
            form_id,
        )
        records, new_etag = self._fetch_form_odata(form_id=form_id, etag=etag)
        if records is None:
            return None, new_etag
        df = self._build_form_submissions_dataframe(form_id=form_id, records=records)
        return df, new_etag

    def fetch_submissions(
        self,
        etags: Optional[dict[str, str]] = None,
    ) -> tuple[dict[str, Optional[pd.DataFrame]], dict[str, Optional[str]]]:
        """Fetch submissions for all accessible forms.

        Returns a per-form mapping where a `None` frame means the endpoint
        returned 304 Not Modified for that form.
        """
        self._require_login()
        existing_etags = etags or {}
        form_frames: dict[str, Optional[pd.DataFrame]] = {}
        new_etags: dict[str, Optional[str]] = {}
        for form in self._list_forms():
            form_id = form["xmlFormId"]
            frame, new_etag = self.fetch_form_submissions(
                form_id=form_id,
                etag=existing_etags.get(form_id),
            )
            form_frames[form_id] = frame
            new_etags[form_id] = new_etag
        return form_frames, new_etags

    def _get_form_versions(self, form_id: str) -> list[dict[str, Any]]:
        """List published versions for a form."""
        url = f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}/versions"
        response = self.get(url)
        return response.json()

    def _download_form_xml(self, form_id: str, version: Optional[str] = None) -> str:
        """Download current or versioned XML form definition."""
        if version is None:
            url = f"{self.base_url}/v1/projects/{self.project_id}/forms/{form_id}.xml"
        else:
            url = (
                f"{self.base_url}/v1/projects/{self.project_id}/forms/"
                f"{form_id}/versions/{version}.xml"
            )
        response = self.get(url)
        return response.text

    def get_form_metadata(self) -> dict[str, Any]:
        """Build per-form, per-version variable metadata maps."""
        self._require_login()
        forms_metadata: dict[str, Any] = {}
        current_versions: dict[str, str] = {}

        try:
            for form in self._list_forms():
                form_id = form["xmlFormId"]
                current_versions[form_id] = str(form.get("version", ""))
                forms_metadata[form_id] = {}
                for version_info in self._get_form_versions(form_id):
                    version = str(version_info["version"])
                    xml_content = self._download_form_xml(
                        form_id=form_id, version=version
                    )
                    variable_metadata = self._extract_metadata_from_xml(xml_content)
                    forms_metadata[form_id][version] = {
                        "variables": variable_metadata,
                    }

            return {
                "_forms": forms_metadata,
                "_current_versions": current_versions,
            }
        except Exception:
            logger.exception("Failed to fetch form metadata")
            raise

    @staticmethod
    def dataset_version_from_etags(etags: dict[str, Optional[str]]) -> str:
        """Derive one stable dataset version string from per-form ETags."""
        text = "||".join(
            f"{form_id}:{etags[form_id] or ''}" for form_id in sorted(etags)
        )
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _extract_metadata_from_xml(self, xml_content: str) -> Dict[str, Dict[str, int]]:
        """Extract min/max metadata from XML form definition.

        Args:
            xml_content: XML form definition content

        Returns:
            Dict mapping field names to {"min": int, "max": int}
        """
        import xml.etree.ElementTree as ET

        metadata = {}

        # Parse XML
        root = ET.fromstring(xml_content)

        # Find all bind elements (they contain the constraints)
        # XForms uses namespaces, so we need to handle that
        # The bind elements are in the default namespace
        for bind in root.findall(".//{http://www.w3.org/2002/xforms}bind"):
            nodeset = bind.get("nodeset")
            constraint = bind.get("constraint", "")

            if not nodeset or not constraint:
                continue

            # Extract field name from nodeset (e.g., "/data/bw_migration_1" -> "bw_migration_1")
            field_name = nodeset.split("/")[-1]

            # Parse constraint to extract min/max
            # Constraints in XML are HTML-encoded: ". &gt;= 0 and . &lt;= 5"
            # We need to decode them first
            import html

            constraint_decoded = html.unescape(constraint)

            min_val = None
            max_val = None

            # Extract >= constraints
            ge_match = re.search(r"\.\s*>=\s*(\d+)", constraint_decoded)
            if ge_match:
                min_val = int(ge_match.group(1))

            # Extract <= constraints
            le_match = re.search(r"\.\s*<=\s*(\d+)", constraint_decoded)
            if le_match:
                max_val = int(le_match.group(1))

            if min_val is not None and max_val is not None:
                metadata[field_name] = {"min": min_val, "max": max_val}
            elif min_val is not None:
                # Only min constraint (e.g., age >= 0)
                metadata[field_name] = {"min": min_val}
            elif max_val is not None:
                # Only max constraint
                metadata[field_name] = {"max": max_val}

        return metadata
