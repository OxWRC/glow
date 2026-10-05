import json
import logging
import os
import threading
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

from glow_api.odk_client import ODKClient
from glow_api.settings import settings
from pydantic import BaseModel

from glow_api.utils import log_duration

logger = logging.getLogger(__name__)


class DataFrameWithWhitelists(BaseModel):
    df: pd.DataFrame
    categorical_whitelist: List[str]
    numerical_whitelist: List[str]
    metadata: Dict[str, Any]
    observed_periods: Dict[Optional[str], List[str]]  # school_name -> list of periods

    model_config = {
        "arbitrary_types_allowed": True,
    }


class DataStore:
    """Thread-safe data store for the questionnaire DataFrame."""

    def __init__(
        self,
        odk_client: ODKClient,
        refresh_hours: int,
        cache_path: Optional[Path] = None,
    ) -> None:
        self._odk_client = odk_client
        self._refresh_hours = refresh_hours
        self._cache_path = cache_path
        self._df: pd.DataFrame = pd.DataFrame()
        self._metadata: Dict[str, Any] = {}
        self._response_etags: Dict[str, Optional[str]] = {}
        self._form_frames_cache: Dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
        self._scheduler = BackgroundScheduler()
        self._categorical_whitelist: List[str] = []
        self._numerical_whitelist: List[str] = []
        self._observed_periods: Dict[Optional[str], List[str]] = {}
        self._initial_load_complete = threading.Event()

    def _load(self) -> pd.DataFrame:
        with log_duration("Load data from ODK Central") as log_data:
            form_frames, new_etags = self._odk_client.fetch_submissions(
                etags=self._response_etags
            )

            if (
                all(frame is None for frame in form_frames.values())
                and self._response_etags
            ):
                logger.info("Data unchanged (ETAG match)")
                log_data["etag_matched"] = True
                return self._df

            for form_id, frame in list(form_frames.items()):
                if frame is None and form_id not in self._form_frames_cache:
                    frame, refreshed_etag = self._odk_client.fetch_form_submissions(
                        form_id=form_id, etag=None
                    )
                    form_frames[form_id] = frame
                    new_etags[form_id] = refreshed_etag

            df = self._merge_form_frames(form_frames)
            self._response_etags = new_etags

            metadata_map = self._odk_client.get_form_metadata()
            flat_metadata = self._flatten_current_metadata(metadata_map)
            dataset_version = self._odk_client.dataset_version_from_etags(new_etags)
            self._metadata = {
                **flat_metadata,
                **metadata_map,
                "_response_etags": dict(new_etags),
                "_etag": dataset_version,
            }

            log_data["row_count"] = len(df)
            log_data["original_col_count"] = len(df.columns)
            log_data["metadata_fields"] = len(flat_metadata)
            log_data["forms_seen"] = len(form_frames)

            # Pre-compute derived scores
            df = self._process_loaded_data(df)
            log_data["derived_col_count"] = (
                len(df.columns) - log_data["original_col_count"]
            )

            # Cache if configured
            if self._cache_path:
                self._save_cache(df, new_etags)

            return df

    def _merge_form_frames(
        self,
        form_frames: dict[str, Optional[pd.DataFrame]],
    ) -> pd.DataFrame:
        """Merge changed form frames with cached unchanged raw form slices."""
        merged: list[pd.DataFrame] = []
        for form_id, frame in form_frames.items():
            if frame is not None:
                self._form_frames_cache[form_id] = frame.copy()
                merged.append(frame)
                continue

            cached = self._form_frames_cache.get(form_id)
            if cached is not None and not cached.empty:
                merged.append(cached.copy())

        if not merged:
            return pd.DataFrame()
        return pd.concat(merged, ignore_index=True, sort=False)

    @staticmethod
    def _flatten_current_metadata(
        metadata_map: dict[str, Any],
    ) -> dict[str, dict[str, int]]:
        """Flatten current-version variable metadata for legacy query consumers.

        If multiple forms define the same raw variable name, prefer non-demographics
        forms before the configured demographics form.
        """
        flat: dict[str, dict[str, int]] = {}
        forms = metadata_map.get("_forms", {})
        current_versions = metadata_map.get("_current_versions", {})
        ordered_form_ids = sorted(
            forms,
            key=lambda form_id: (form_id == settings.ODK_DEMOGRAPHICS_FORM_ID, form_id),
        )

        for form_id in ordered_form_ids:
            versions = forms[form_id]
            current_version = current_versions.get(form_id)
            if not current_version:
                continue
            version_payload = versions.get(current_version, {})
            for variable, variable_metadata in version_payload.get(
                "variables", {}
            ).items():
                flat.setdefault(variable, variable_metadata)
        return flat

    def _process_loaded_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process loaded data by normalizing, computing derived scores, and extracting metadata.

        This method is called once during data loading to prepare the analytic dataset.
        """
        from glow_api.normalization import normalize_submissions

        if not hasattr(self, "_observed_periods"):
            self._observed_periods = {}

        # Normalize submissions (add period_id column)
        df = normalize_submissions(df)

        # Materialize one analytic row per uid+school+period for real multi-form ODK data.
        if "__xmlFormId" in df.columns and "createdAt" in df.columns:
            df = self._materialize_analytic_rows(df)

        # Compute derived scores
        df = self._compute_derived_scores(df)

        # Extract whitelists
        self._extract_whitelists(df)

        # Pre-compute observed periods for dataset-scoped and each school
        self._compute_observed_periods(df)

        return df

    def _compute_observed_periods(self, df: pd.DataFrame) -> None:
        """Pre-compute observed periods for the dataset and each school.

        Stores results in self._observed_periods:
        - None key: dataset-scoped periods
        - school_name keys: school-scoped periods
        """
        from glow_api.normalization import get_observed_periods

        # Dataset-scoped periods
        self._observed_periods[None] = get_observed_periods(df)

        # School-scoped periods
        if "school" in df.columns:
            for school_name in df["school"].dropna().unique():
                self._observed_periods[str(school_name)] = get_observed_periods(
                    df, school_name=str(school_name)
                )

    def _extract_whitelists(self, df: pd.DataFrame) -> None:
        categorical: List[str] = [
            "yearGroup",
            "class",
            "sex",
            "d_sex",
            "ethnicity",
            "d_ethnicity",
            "d_age",
            "d_city",
            "d_country",
        ]
        numerical: List[str] = []
        for col in df.columns:
            variable_part = col.split("__", 1)[1] if "__" in col else col
            split = variable_part.split("_")
            if len(split) > 1 and split[0] in settings.DATA_PREFIXES:
                numerical.append(col)
        self._categorical_whitelist = categorical
        self._numerical_whitelist = numerical

    def _materialize_analytic_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Collapse multi-form submission rows into one analytic row per period.

        This is the real multi-form path used for ODK-loaded data. Legacy tests and
        fixtures that do not contain form tags or createdAt timestamps skip this step.
        """
        if df.empty:
            return df

        group_keys = ["uid", "period_id"]
        if "school" in df.columns:
            group_keys.append("school")

        working = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(working["createdAt"]):
            working["createdAt"] = pd.to_datetime(
                working["createdAt"], utc=True, errors="coerce"
            )

        latest_created = (
            working.groupby(group_keys, dropna=False)["createdAt"].max().reset_index()
        )
        result = latest_created

        demographics_fields = [
            col
            for col in working.columns
            if col == "yearGroup"
            or any(
                col.startswith(f"{prefix}_")
                for prefix in settings.DATA_DEMOGRAPHIC_PREFIXES
            )
        ]

        if demographics_fields:
            demo_rows = working[
                working["__xmlFormId"] == settings.ODK_DEMOGRAPHICS_FORM_ID
            ]
            if not demo_rows.empty:
                collapsed_demo = self._collapse_latest_non_null(
                    demo_rows,
                    group_keys,
                    demographics_fields,
                )
                result = result.merge(collapsed_demo, on=group_keys, how="left")

        housekeeping = {
            *group_keys,
            "createdAt",
            "updatedAt",
            "SubmissionDate",
            "__xmlFormId",
            "__id",
            "__system/submissionDate",
            "meta/instanceID",
        }

        for form_id in sorted(working["__xmlFormId"].dropna().unique()):
            form_rows = working[working["__xmlFormId"] == form_id].copy()
            if form_rows.empty:
                continue

            if form_id == settings.ODK_DEMOGRAPHICS_FORM_ID:
                continue

            rename_map: Dict[str, str] = {}
            namespaced_fields: list[str] = []
            for col in form_rows.columns:
                if col in housekeeping or col in demographics_fields:
                    continue
                if col.startswith("__"):
                    continue
                namespaced = f"{form_id}__{col}"
                rename_map[col] = namespaced
                namespaced_fields.append(namespaced)

            if "__version" in form_rows.columns:
                rename_map["__version"] = f"{form_id}__version"
                namespaced_fields.append(f"{form_id}__version")

            if not namespaced_fields:
                continue

            form_rows = form_rows.rename(columns=rename_map)
            collapsed_form = self._collapse_latest_non_null(
                form_rows,
                group_keys,
                namespaced_fields,
            )
            result = result.merge(collapsed_form, on=group_keys, how="left")

        return result

    @staticmethod
    def _collapse_latest_non_null(
        df: pd.DataFrame,
        group_keys: list[str],
        value_columns: list[str],
    ) -> pd.DataFrame:
        """Collapse rows by taking latest non-null value per field within group."""
        if df.empty:
            return pd.DataFrame(columns=group_keys + value_columns)

        ordered = df.sort_values("createdAt", ascending=False, kind="stable")
        present = [col for col in value_columns if col in ordered.columns]
        missing = [col for col in value_columns if col not in ordered.columns]

        grouped = ordered.groupby(group_keys, dropna=False, sort=False)
        if present:
            collapsed = grouped[present].first().reset_index()
        else:
            collapsed = grouped.size().reset_index()[group_keys]

        for col in missing:
            collapsed[col] = None

        return collapsed[group_keys + value_columns]

    def _compute_derived_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add derived score columns to the DataFrame.

        Computes subscale totals and scale totals for all BeeWell questionnaires
        where it makes sense to aggregate items.
        """
        # Define questionnaires that should have total scores computed and the items that comprise them
        questionnaires_for_totals: Dict[str, List[str]] = {}
        for c in df.columns:
            namespace = ""
            variable_part = c
            if "__" in c:
                namespace, variable_part = c.split("__", 1)
            split = variable_part.split("_")
            if len(split) < 2 or split[0] not in settings.DATA_PREFIXES:
                continue
            if variable_part == "version":
                continue
            if len(split) < 2:
                continue
            ss = "_".join(split[1:-1])
            if not ss:
                continue
            if namespace:
                subscale = f"{namespace}__{split[0]}_{ss}_total"
            else:
                subscale = f"{split[0]}_{ss}_total"
            if subscale in questionnaires_for_totals:
                questionnaires_for_totals[subscale].append(c)
            else:
                questionnaires_for_totals[subscale] = [c]

        # Collect all computed subscale scores to avoid DataFrame fragmentation
        computed_scores = {}

        for subscale, columns in questionnaires_for_totals.items():
            # Only compute if the column doesn't already exist
            if subscale not in df.columns:
                computed_scores[subscale] = df[columns].sum(axis=1, skipna=True)
                logger.debug("Computed %s from %d columns", subscale, len(columns))
            else:
                logger.debug("Skipping %s - already exists in data", subscale)

        # Concatenate all computed scores at once instead of iteratively assigning
        if computed_scores:
            df = pd.concat([df, pd.DataFrame(computed_scores)], axis=1)
            logger.info("Computed %d subscale/scale total scores", len(computed_scores))
        else:
            logger.warning("No questionnaire columns found to compute total scores")

        return df

    def _save_cache(self, df: pd.DataFrame, etags: dict[str, Optional[str]]) -> None:
        """Save DataFrame and ETAG to cache."""
        if not self._cache_path:
            return

        try:
            cache_dir = self._cache_path.parent
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Save DataFrame
            df.to_parquet(self._cache_path)

            etag_path = self._cache_path.with_suffix(".etag")
            etag_path.write_text(json.dumps(etags))

            logger.info("Cached data to %s", self._cache_path)
        except Exception:
            logger.exception("Failed to save cache")

    def _load_cache(self) -> Optional[Tuple[pd.DataFrame, dict[str, Optional[str]]]]:
        """Load DataFrame and per-form ETAGs from cache.

        Returns:
            Tuple of (DataFrame, ETAG map) or None if cache doesn't exist
        """
        if not self._cache_path or not self._cache_path.exists():
            return None

        try:
            df = pd.read_parquet(self._cache_path)

            # Load ETAG map if it exists
            etag_path = self._cache_path.with_suffix(".etag")
            etag_text = etag_path.read_text() if etag_path.exists() else "{}"
            etags = json.loads(etag_text)

            logger.info("Loaded cached data from %s", self._cache_path)
            return df, etags
        except Exception:
            logger.exception("Failed to load cache")
            return None

    def refresh(self) -> None:
        """Reload data from ODK Central, replacing the in-memory DataFrame."""
        logger.info("Refreshing data from ODK Central")
        try:
            df = self._load()
        except Exception:
            logger.warning(
                "Failed to load data from ODK Central; keeping previous data",
                exc_info=True,
            )
            # Serve stale data on error (graceful degradation)
            return
        with self._lock:
            self._df = df
        logger.info("Data refreshed: %d rows, %d columns", len(df), len(df.columns))

    def to_frozen(self) -> DataFrameWithWhitelists:
        """Return a snapshot of the current DataFrame."""
        with self._lock:
            return DataFrameWithWhitelists(
                df=self._df.copy(),
                categorical_whitelist=self._categorical_whitelist,
                numerical_whitelist=self._numerical_whitelist,
                metadata=copy.deepcopy(self._metadata),
                observed_periods=self._observed_periods.copy(),
            )

    def startup(self) -> None:
        """Initial load and schedule periodic refresh."""
        # Try loading from cache first
        cached = self._load_cache()
        if cached:
            df, etags = cached
            self._extract_whitelists(df)
            self._compute_observed_periods(df)
            with self._lock:
                self._df = df
                self._response_etags = etags
            logger.info(
                "Loaded from cache: %d rows, %d columns", len(df), len(df.columns)
            )

        def _initial_refresh() -> None:
            try:
                self.refresh()
            finally:
                self._initial_load_complete.set()

        threading.Thread(
            target=_initial_refresh, daemon=True, name="datastore-initial-refresh"
        ).start()

        if self._refresh_hours > 0:
            self._scheduler.add_job(
                self.refresh,
                trigger="interval",
                hours=self._refresh_hours,
                id="data_refresh",
            )
            logger.info("Data refresh scheduled every %d hour(s)", self._refresh_hours)
            self._scheduler.start()

    def shutdown(self) -> None:
        """Stop the background scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Data refresh scheduler stopped")


# Module-level singleton (lazy initialization)
_odk_client: Optional[ODKClient] = None
datastore: Optional[DataStore] = None


def _init_singleton():
    """Initialize the module-level singleton DataStore."""
    global _odk_client, datastore

    if datastore is not None:
        return

    _odk_client = ODKClient(
        base_url=settings.ODK_API_URL,
        username=settings.ODK_API_EMAIL,
        password=settings.ODK_API_PASSWORD,
        project_id=settings.ODK_PROJECT_ID,
        verify_ssl=os.getenv("GLOW_ODK_VERIFY_SSL", "true").lower() != "false",
        host_header=settings.ODK_HOST_HEADER,
    )

    datastore = DataStore(
        odk_client=_odk_client,
        refresh_hours=settings.DATA_REFRESH_HOURS,
        cache_path=Path(settings.DATA_CACHE_PATH) if settings.DATA_CACHE_PATH else None,
    )


def get_datastore() -> DataStore:
    """Dependency function that returns the current datastore instance.

    This allows tests to override the datastore by reassigning the module-level
    'datastore' variable, and the routers will pick up the new instance.
    """
    if datastore is None:
        _init_singleton()
    return datastore
