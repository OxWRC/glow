"""Slim, hourly ODK poll behind /query/counts and /export/counts.

Live collection progress between vintage cuts needs only who submitted, at
which school, and when - so this asks ODK for just those fields ($select)
rather than every answer. The full DataStore is fetched only at startup and
on vintage cuts; nothing else polls it.
"""

import logging
import threading
from typing import Optional

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

from glow_api.data import get_odk_client
from glow_api.normalization import normalize_submissions
from glow_api.odk_client import ODKClient
from glow_api.settings import settings
from glow_api.utils import log_duration

logger = logging.getLogger(__name__)

# period_id derives from the submission date. ODK Central rejects the whole
# request (400) if any form lacks a selected field, so every form must carry
# uid and school - all of odk-forms/ do.
COUNT_FIELDS = ["__id", "__system/submissionDate", "uid", "school"]
_COLUMNS = ["uid", "school", "period_id"]


class CountsStore:
    """Thread-safe uid/school/period_id frame, refreshed on an interval."""

    def __init__(self, odk_client: ODKClient, refresh_hours: int) -> None:
        self._odk_client = odk_client
        self._refresh_hours = refresh_hours
        self._frame = pd.DataFrame(columns=_COLUMNS)
        self._etags: dict[str, Optional[str]] = {}
        self._form_frames: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
        self._scheduler = BackgroundScheduler()

    def _load(self) -> pd.DataFrame:
        with log_duration("Load count fields from ODK Central") as log_data:
            form_frames, new_etags = self._odk_client.fetch_submissions(
                etags=self._etags, select=COUNT_FIELDS
            )
            for form_id, frame in form_frames.items():
                if frame is None and form_id not in self._form_frames:
                    # 304 for a form we hold no rows for: refetch it outright.
                    frame, new_etags[form_id] = self._odk_client.fetch_form_submissions(
                        form_id=form_id, select=COUNT_FIELDS
                    )
                if frame is not None:
                    self._form_frames[form_id] = self._slim(frame)
            # Forms no longer listed drop out, same as the full DataStore.
            self._form_frames = {
                form_id: self._form_frames[form_id]
                for form_id in form_frames
                if form_id in self._form_frames
            }
            self._etags = new_etags

            frames = [f for f in self._form_frames.values() if not f.empty]
            if not frames:
                return pd.DataFrame(columns=_COLUMNS)
            combined = pd.concat(frames, ignore_index=True)
            log_data["row_count"] = len(combined)
            return combined

    @staticmethod
    def _slim(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=_COLUMNS)
        normalized = normalize_submissions(frame)
        return normalized.reindex(columns=_COLUMNS)

    def refresh(self) -> None:
        """Re-poll ODK; keeps the previous counts if it fails."""
        try:
            frame = self._load()
        except Exception:
            logger.warning(
                "Failed to load counts from ODK Central; keeping previous counts",
                exc_info=True,
            )
            return
        with self._lock:
            self._frame = frame

    def frame(self) -> pd.DataFrame:
        with self._lock:
            return self._frame.copy()

    def startup(self) -> None:
        """Initial poll in the background, then every refresh_hours."""
        threading.Thread(
            target=self.refresh, daemon=True, name="counts-initial-refresh"
        ).start()
        if self._refresh_hours > 0:
            self._scheduler.add_job(
                self.refresh,
                trigger="interval",
                hours=self._refresh_hours,
                id="counts_refresh",
            )
            self._scheduler.start()
            logger.info(
                "Counts refresh scheduled every %d hour(s)", self._refresh_hours
            )

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)


counts_store: Optional[CountsStore] = None


def get_counts_store() -> CountsStore:
    global counts_store

    if counts_store is None:
        counts_store = CountsStore(
            odk_client=get_odk_client(),
            refresh_hours=settings.DATA_REFRESH_HOURS,
        )
    return counts_store
