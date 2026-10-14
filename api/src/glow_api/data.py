import logging
import os
import threading
import copy
from typing import Any, Dict, List, Optional

import pandas as pd

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


class ODKFetchError(RuntimeError):
    """A strict refresh couldn't fetch from ODK Central - main.py maps it to a 502."""


class DataStore:
    """Thread-safe data store for the full questionnaire DataFrame.

    Empty until the first vintage cut, and refreshed only by cuts (see
    vintages.cut_vintage): never at startup, never polled. Live collection
    progress comes from the slim CountsStore instead.
    """

    def __init__(self, odk_client: ODKClient) -> None:
        self._odk_client = odk_client
        self._df: pd.DataFrame = pd.DataFrame()
        self._metadata: Dict[str, Any] = {}
        self._response_etags: Dict[str, Optional[str]] = {}
        self._form_frames_cache: Dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
        # Serialises whole loads: _load mutates ETag/form-frame state, and
        # two cuts (scheduled + manual) can overlap.
        self._refresh_lock = threading.Lock()
        self._categorical_whitelist: List[str] = []
        self._numerical_whitelist: List[str] = []
        self._observed_periods: Dict[Optional[str], List[str]] = {}

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

    def refresh(self, strict: bool = False) -> None:
        """Reload data from ODK Central, replacing the in-memory DataFrame.

        On failure the previous data is kept; strict callers (vintage cuts,
        which mustn't freeze stale data unnoticed) also get an ODKFetchError.
        """
        logger.info("Refreshing data from ODK Central")
        try:
            with self._refresh_lock:
                df = self._load()
        except Exception as exc:
            logger.warning(
                "Failed to load data from ODK Central; keeping previous data",
                exc_info=True,
            )
            if strict:
                raise ODKFetchError(f"Could not fetch data from ODK Central: {exc}")
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


# Module-level singleton (lazy initialization)
_odk_client: Optional[ODKClient] = None
datastore: Optional[DataStore] = None


def get_odk_client() -> ODKClient:
    """The process-wide ODK client, shared by DataStore and CountsStore."""
    global _odk_client

    if _odk_client is None:
        _odk_client = ODKClient(
            base_url=settings.ODK_API_URL,
            username=settings.ODK_API_EMAIL,
            password=settings.ODK_API_PASSWORD,
            project_id=settings.ODK_PROJECT_ID,
            verify_ssl=os.getenv("GLOW_ODK_VERIFY_SSL", "true").lower() != "false",
            host_header=settings.ODK_HOST_HEADER,
        )
    return _odk_client


def _init_singleton():
    """Initialize the module-level singleton DataStore."""
    global datastore

    if datastore is not None:
        return

    datastore = DataStore(odk_client=get_odk_client())


def get_datastore() -> DataStore:
    """Dependency function that returns the current datastore instance.

    This allows tests to override the datastore by reassigning the module-level
    'datastore' variable, and the routers will pick up the new instance.
    """
    if datastore is None:
        _init_singleton()
    return datastore
