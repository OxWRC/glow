"""Mock ODK Client for testing."""

import copy
import hashlib
from typing import Any, Dict, Optional

import pandas as pd


class MockODKClient:
    """Mock ODK Client that returns predefined test data."""

    def __init__(
        self,
        submissions_df: Optional[pd.DataFrame] = None,
        metadata: Optional[Dict[str, Any]] = None,
        etag: Optional[str] = None,
        submissions_by_form: Optional[Dict[str, pd.DataFrame]] = None,
        response_etags: Optional[Dict[str, Optional[str]]] = None,
    ):
        """Initialize mock ODK client.

        Args:
            submissions_df: DataFrame to return from fetch_submissions()
            metadata: Metadata dict to return from get_form_metadata()
            etag: ETAG to return (None means no ETAG support)
        """
        if submissions_by_form is not None:
            self.submissions_by_form = {
                form_id: frame.copy() for form_id, frame in submissions_by_form.items()
            }
        else:
            default_df = (
                submissions_df.copy() if submissions_df is not None else pd.DataFrame()
            )
            if not default_df.empty and "__xmlFormId" not in default_df.columns:
                default_df["__xmlFormId"] = "bewell_questionnaire"
            self.submissions_by_form = {"bewell_questionnaire": default_df}

        if metadata is None:
            metadata = {}
        if "_forms" in metadata:
            self.metadata = metadata
        else:
            self.metadata = {
                "_forms": {
                    "bewell_questionnaire": {
                        "1": {"variables": metadata.copy()},
                    }
                },
                "_current_versions": {"bewell_questionnaire": "1"},
            }

        if response_etags is not None:
            self.response_etags = response_etags.copy()
        else:
            self.response_etags = {
                form_id: etag for form_id in self.submissions_by_form
            }
        self.fetch_count = 0
        self.metadata_fetch_count = 0
        self.list_forms_count = 0
        self.last_select: Optional[list[str]] = None
        self.last_etags: Dict[str, str] = {}

    @property
    def submissions_df(self) -> pd.DataFrame:
        return self.submissions_by_form.get(
            "bewell_questionnaire", pd.DataFrame()
        ).copy()

    @submissions_df.setter
    def submissions_df(self, value: pd.DataFrame) -> None:
        df = value.copy()
        if not df.empty and "__xmlFormId" not in df.columns:
            df["__xmlFormId"] = "bewell_questionnaire"
        self.submissions_by_form["bewell_questionnaire"] = df

    def _list_forms(self) -> list[dict[str, Any]]:
        self.list_forms_count += 1
        current_versions = self.metadata.get("_current_versions", {})
        return [
            {"xmlFormId": form_id, "version": current_versions.get(form_id, "1")}
            for form_id in self.submissions_by_form
        ]

    def fetch_submissions(
        self,
        etags: Optional[Dict[str, str]] = None,
        select: Optional[list[str]] = None,
    ) -> tuple[Dict[str, Optional[pd.DataFrame]], Dict[str, Optional[str]]]:
        """Mock multi-form fetch_submissions."""
        self.fetch_count += 1
        self.last_select = select
        existing_etags = etags or {}
        self.last_etags = dict(existing_etags)
        frames: Dict[str, Optional[pd.DataFrame]] = {}
        new_etags: Dict[str, Optional[str]] = {}
        for form_id, df in self.submissions_by_form.items():
            current_etag = self.response_etags.get(form_id)
            if (
                existing_etags.get(form_id)
                and existing_etags.get(form_id) == current_etag
            ):
                frames[form_id] = None
                new_etags[form_id] = current_etag
            else:
                frame = df.copy()
                if not frame.empty and "__xmlFormId" not in frame.columns:
                    frame["__xmlFormId"] = form_id
                frames[form_id] = frame
                new_etags[form_id] = current_etag
        return frames, new_etags

    def get_form_metadata(self) -> Dict[str, Any]:
        """Mock get_form_metadata that returns predefined metadata map."""
        self.metadata_fetch_count += 1
        return copy.deepcopy(self.metadata)

    def dataset_version_from_etags(self, etags: Dict[str, Optional[str]]) -> str:
        text = "||".join(f"{form}:{etags[form] or ''}" for form in sorted(etags))
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _download_xlsform(self) -> bytes:
        """Mock download_xlsform (not used in tests)."""
        raise NotImplementedError("Mock download_xlsform not implemented")

    def _extract_metadata_from_xlsform(
        self, xlsform_bytes: bytes
    ) -> Dict[str, Dict[str, int]]:
        """Mock extract_metadata_from_xlsform (not used in tests)."""
        raise NotImplementedError("Mock extract_metadata_from_xlsform not implemented")
