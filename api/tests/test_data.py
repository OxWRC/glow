"""Tests for the DataStore class and derived score computation."""

import tempfile
import threading
from pathlib import Path

import pandas as pd
import pytest

import glow_api.data as data_module
from glow_api.data import DataStore
from glow_api.settings import settings
from tests.mock_odk import MockODKClient


class TestComputeDerivedScores:
    """Test the _compute_derived_scores method."""

    def test_single_subscale_with_three_items(self):
        """Test computing a single subscale total from three items."""
        # Create a DataFrame with three items in one subscale
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "bw_wbeing_1": [3, 4, 2],
                "bw_wbeing_2": [4, 3, 3],
                "bw_wbeing_3": [2, 4, 3],
            }
        )

        # Create a DataStore instance and compute derived scores
        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Verify the total column was created
        assert "bw_wbeing_total" in result.columns

        # Verify the totals are computed correctly
        assert result.loc[0, "bw_wbeing_total"] == 9  # 3 + 4 + 2
        assert result.loc[1, "bw_wbeing_total"] == 11  # 4 + 3 + 4
        assert result.loc[2, "bw_wbeing_total"] == 8  # 2 + 3 + 3

    def test_multiple_subscales(self):
        """Test computing totals for multiple subscales."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
                "bw_stress_1": [2, 3],
                "bw_stress_2": [1, 2],
                "bw_stress_3": [3, 4],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Verify both total columns were created
        assert "bw_wbeing_total" in result.columns
        assert "bw_stress_total" in result.columns

        # Verify the totals
        assert result.loc[0, "bw_wbeing_total"] == 7  # 3 + 4
        assert result.loc[1, "bw_wbeing_total"] == 7  # 4 + 3
        assert result.loc[0, "bw_stress_total"] == 6  # 2 + 1 + 3
        assert result.loc[1, "bw_stress_total"] == 9  # 3 + 2 + 4

    def test_handles_missing_values(self):
        """Test that missing values are handled correctly with skipna=True."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "bw_wbeing_1": [3, None, 2],
                "bw_wbeing_2": [4, 3, None],
                "bw_wbeing_3": [2, 4, 3],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Verify totals skip NaN values
        assert result.loc[0, "bw_wbeing_total"] == 9  # 3 + 4 + 2
        assert result.loc[1, "bw_wbeing_total"] == 7  # 0 (NaN) + 3 + 4
        assert result.loc[2, "bw_wbeing_total"] == 5  # 2 + 0 (NaN) + 3

    def test_all_missing_values_returns_zero(self):
        """Test that all missing values in a row result in a total of 0."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "bw_wbeing_1": [3, None],
                "bw_wbeing_2": [4, None],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        assert result.loc[0, "bw_wbeing_total"] == 7  # 3 + 4
        assert result.loc[1, "bw_wbeing_total"] == 0  # NaN + NaN = 0 with skipna=True

    def test_preserves_original_columns(self):
        """Test that original columns are preserved after computing derived scores."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 2],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Verify original columns still exist
        assert "uid" in result.columns
        assert "wave" in result.columns
        assert "bw_wbeing_1" in result.columns
        assert "bw_wbeing_2" in result.columns

        # Verify original data is unchanged
        assert list(result["uid"]) == ["S001", "S002"]
        assert list(result["wave"]) == [1, 2]

    def test_no_questionnaire_columns(self):
        """Test behavior when there are no questionnaire columns to process."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 2],
                "school": ["School A", "School B"],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Should return the original DataFrame unchanged
        assert list(result.columns) == ["uid", "wave", "school"]
        assert len(result) == 2

    def test_different_prefix(self):
        """Test computing scores for a different prefix (if configured)."""
        # Temporarily modify settings to include another prefix
        original_prefixes = settings.DATA_PREFIXES.copy()
        settings.DATA_PREFIXES = ["bw", "test"]

        try:
            df = pd.DataFrame(
                {
                    "uid": ["S001", "S002"],
                    "test_anxiety_1": [3, 4],
                    "test_anxiety_2": [2, 3],
                    "bw_wbeing_1": [4, 5],
                }
            )

            ds = DataStore.__new__(DataStore)
            result = ds._compute_derived_scores(df)

            # Verify both prefixes generate totals
            assert "test_anxiety_total" in result.columns
            assert "bw_wbeing_total" in result.columns

            assert result.loc[0, "test_anxiety_total"] == 5  # 3 + 2
            assert result.loc[1, "test_anxiety_total"] == 7  # 4 + 3
            assert result.loc[0, "bw_wbeing_total"] == 4
            assert result.loc[1, "bw_wbeing_total"] == 5
        finally:
            # Restore original settings
            settings.DATA_PREFIXES = original_prefixes

    def test_comprehensive_beewell_example(self):
        """Test with a realistic BeeWell dataset containing multiple subscales."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "wave": [1, 1, 1],
                "school": ["School A", "School A", "School B"],
                # Well-being subscale
                "bw_wbeing_1": [3, 4, 2],
                "bw_wbeing_2": [4, 3, 3],
                "bw_wbeing_3": [2, 4, 3],
                # Stress subscale
                "bw_stress_1": [2, 3, 1],
                "bw_stress_2": [1, 2, 2],
                # Self-esteem subscale
                "bw_selfest_1": [4, 4, 3],
                "bw_selfest_2": [3, 3, 4],
                "bw_selfest_3": [4, 4, 3],
                "bw_selfest_4": [3, 3, 4],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        # Verify all subscale totals are created
        assert "bw_wbeing_total" in result.columns
        assert "bw_stress_total" in result.columns
        assert "bw_selfest_total" in result.columns

        # Verify computations for first student
        assert result.loc[0, "bw_wbeing_total"] == 9  # 3 + 4 + 2
        assert result.loc[0, "bw_stress_total"] == 3  # 2 + 1
        assert result.loc[0, "bw_selfest_total"] == 14  # 4 + 3 + 4 + 3

        # Verify computations for second student
        assert result.loc[1, "bw_wbeing_total"] == 11  # 4 + 3 + 4
        assert result.loc[1, "bw_stress_total"] == 5  # 3 + 2
        assert result.loc[1, "bw_selfest_total"] == 14  # 4 + 3 + 4 + 3

    def test_floating_point_values(self):
        """Test that floating point values are summed correctly."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "bw_wbeing_1": [3.5, 4.2],
                "bw_wbeing_2": [2.7, 3.8],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        assert result.loc[0, "bw_wbeing_total"] == pytest.approx(6.2)  # 3.5 + 2.7
        assert result.loc[1, "bw_wbeing_total"] == pytest.approx(8.0)  # 4.2 + 3.8

    def test_zero_values(self):
        """Test that zero values are included in the sum."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "bw_wbeing_1": [0, 4],
                "bw_wbeing_2": [3, 0],
                "bw_wbeing_3": [0, 0],
            }
        )

        ds = DataStore.__new__(DataStore)
        result = ds._compute_derived_scores(df)

        assert result.loc[0, "bw_wbeing_total"] == 3  # 0 + 3 + 0
        assert result.loc[1, "bw_wbeing_total"] == 4  # 4 + 0 + 0


class TestDataStore:
    """Test the DataStore class functionality."""

    def test_init(self):
        """Test DataStore initialization."""
        mock_client = MockODKClient()
        ds = DataStore(odk_client=mock_client, refresh_hours=24)

        assert ds._odk_client == mock_client
        assert ds._refresh_hours == 24
        assert isinstance(ds._df, pd.DataFrame)
        assert ds._df.empty
        assert isinstance(ds._lock, type(threading.Lock()))
        assert ds._metadata == {}
        assert ds._response_etags == {}

    def test_load_from_odk(self):
        """Test loading data from ODK Central (mocked)."""
        # Create test data
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )
        metadata = {
            "bw_wbeing_1": {"min": 0, "max": 5},
            "bw_wbeing_2": {"min": 0, "max": 5},
        }

        mock_client = MockODKClient(submissions_df=df, metadata=metadata)
        ds = DataStore(odk_client=mock_client, refresh_hours=0)

        loaded_df = ds._load()

        # Verify data was loaded
        assert len(loaded_df) == 2
        assert "uid" in loaded_df.columns
        assert "bw_wbeing_1" in loaded_df.columns

        # Verify derived scores were computed
        assert "bw_wbeing_total" in loaded_df.columns
        assert loaded_df.loc[0, "bw_wbeing_total"] == 7  # 3 + 4
        assert loaded_df.loc[1, "bw_wbeing_total"] == 7  # 4 + 3

        # Verify metadata was fetched
        assert ds._metadata["bw_wbeing_1"] == metadata["bw_wbeing_1"]
        assert ds._metadata["bw_wbeing_2"] == metadata["bw_wbeing_2"]
        assert ds._metadata["_current_versions"] == {"bewell_questionnaire": "1"}
        assert mock_client.fetch_count == 1
        assert mock_client.metadata_fetch_count == 1

    def test_load_with_etag_match(self):
        """Test that ETAG matching returns cached data."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        mock_client = MockODKClient(submissions_df=df, etag="test-etag-123")
        ds = DataStore(odk_client=mock_client, refresh_hours=0)

        # First load
        df1 = ds._load()
        assert len(df1) == 2
        assert ds._response_etags == {"bewell_questionnaire": "test-etag-123"}

        # Simulate what refresh() does - store the loaded data
        ds._df = df1

        # Second load with same ETAG - should return existing data from self._df
        df2 = ds._load()
        assert len(df2) == 2
        # ETAG matched, so ODK returned empty DataFrame, but we kept existing data
        assert mock_client.fetch_count == 2

    def test_refresh(self):
        """Test refreshing data from ODK Central."""
        # Initial data
        df1 = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        mock_client = MockODKClient(submissions_df=df1, metadata={})
        ds = DataStore(odk_client=mock_client, refresh_hours=0)
        ds.refresh()

        # Verify initial data
        frozen = ds.to_frozen()
        assert len(frozen.df) == 2
        assert frozen.df.loc[0, "bw_wbeing_total"] == 7

        # Update mock to return new data
        df2 = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "wave": [1, 1, 1],
                "bw_wbeing_1": [5, 6, 7],
                "bw_wbeing_2": [5, 6, 7],
            }
        )
        mock_client.submissions_df = df2

        # Refresh and verify new data
        ds.refresh()
        frozen = ds.to_frozen()
        assert len(frozen.df) == 3
        assert frozen.df.loc[0, "bw_wbeing_total"] == 10  # 5 + 5
        assert frozen.df.loc[1, "bw_wbeing_total"] == 12  # 6 + 6
        assert frozen.df.loc[2, "bw_wbeing_total"] == 14  # 7 + 7

    def test_refresh_odk_error(self):
        """Test that refresh handles ODK errors gracefully."""

        # Create a mock that raises an error
        class ErrorODKClient(MockODKClient):
            def fetch_submissions(self, etags=None):
                raise RuntimeError("ODK Central connection failed")

        mock_client = ErrorODKClient()
        ds = DataStore(odk_client=mock_client, refresh_hours=0)

        # Set initial data
        ds._df = pd.DataFrame({"uid": ["S001"]})

        # Refresh should not crash and should keep existing data
        ds.refresh()
        frozen = ds.to_frozen()
        assert len(frozen.df) == 1
        assert "uid" in frozen.df.columns

    def test_refresh_odk_error_logs_warning(self, monkeypatch):
        """Test that failed refreshes are logged at warning level."""

        class ErrorODKClient(MockODKClient):
            def fetch_submissions(self, etags=None):
                raise RuntimeError("ODK Central connection failed")

        mock_client = ErrorODKClient()
        ds = DataStore(odk_client=mock_client, refresh_hours=0)

        warnings = []

        def fake_warning(message, *args, **kwargs):
            warnings.append((message, args, kwargs))

        monkeypatch.setattr(data_module.logger, "warning", fake_warning)

        ds.refresh()

        assert len(warnings) == 1
        assert (
            warnings[0][0]
            == "Failed to load data from ODK Central; keeping previous data"
        )
        assert warnings[0][2]["exc_info"] is True

    def test_get_dataframe_returns_copy(self):
        """Test that to_frozen returns a copy, not the original."""
        mock_client = MockODKClient()
        ds = DataStore(odk_client=mock_client, refresh_hours=0)
        ds._df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "bw_wbeing_1": [3, 4],
            }
        )
        ds._metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}

        # Get dataframe and modify it
        frozen1 = ds.to_frozen()
        frozen1.df.loc[0, "bw_wbeing_1"] = 999
        frozen1.metadata["bw_wbeing_1"]["min"] = 999

        # Get dataframe again and verify original is unchanged
        frozen2 = ds.to_frozen()
        assert frozen2.df.loc[0, "bw_wbeing_1"] == 3
        assert frozen2.metadata["bw_wbeing_1"]["min"] == 0

    def test_thread_safety(self):
        """Test that DataStore is thread-safe."""
        import time

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        mock_client = MockODKClient(submissions_df=df, metadata={})
        ds = DataStore(odk_client=mock_client, refresh_hours=0)
        ds.startup()
        assert ds._initial_load_complete.wait(timeout=5)

        results = []
        errors = []

        def read_data():
            try:
                for _ in range(10):
                    frozen = ds.to_frozen()
                    results.append(len(frozen.df))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        # Start multiple threads reading simultaneously
        threads = [threading.Thread(target=read_data) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify no errors occurred
        assert len(errors) == 0
        # Verify all reads returned consistent data
        assert all(r == 2 for r in results)

        ds.shutdown()

    def test_startup_and_shutdown(self):
        """Test startup and shutdown methods."""
        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "wave": [1],
                "bw_wbeing_1": [3],
            }
        )

        mock_client = MockODKClient(submissions_df=df, metadata={})
        ds = DataStore(odk_client=mock_client, refresh_hours=1)

        # Verify data is not loaded yet
        assert ds._df.empty

        # Startup schedules the initial refresh in the background (non-blocking)
        ds.startup()
        assert ds._initial_load_complete.wait(timeout=5)
        assert not ds._df.empty
        assert len(ds._df) == 1

        # Shutdown should stop scheduler
        ds.shutdown()
        assert not ds._scheduler.running

    def test_startup_with_zero_refresh_hours(self):
        """Test that scheduler is not started when refresh_hours is 0."""
        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "wave": [1],
                "bw_wbeing_1": [3],
            }
        )

        mock_client = MockODKClient(submissions_df=df, metadata={})
        ds = DataStore(odk_client=mock_client, refresh_hours=0)
        ds.startup()

        # Verify data is loaded once the background refresh completes
        assert ds._initial_load_complete.wait(timeout=5)
        assert not ds._df.empty

        # Verify scheduler is not running (no recurring job scheduled)
        assert not ds._scheduler.running

    def test_startup_odk_error_logs_warning_and_keeps_empty_dataframe(
        self, monkeypatch
    ):
        """Test startup tolerates ODK failures and logs refresh warnings."""

        class ErrorODKClient(MockODKClient):
            def fetch_submissions(self, etags=None):
                raise RuntimeError("ODK Central connection failed")

        ds = DataStore(odk_client=ErrorODKClient(), refresh_hours=0)

        warnings = []

        def fake_warning(message, *args, **kwargs):
            warnings.append((message, args, kwargs))

        monkeypatch.setattr(data_module.logger, "warning", fake_warning)

        ds.startup()

        assert ds._initial_load_complete.wait(timeout=5)
        assert ds._df.empty
        assert len(warnings) == 1
        assert (
            warnings[0][0]
            == "Failed to load data from ODK Central; keeping previous data"
        )
        assert warnings[0][2]["exc_info"] is True

    def test_cache_save_and_load(self):
        """Test that cache is saved and loaded correctly."""
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.parquet"

            mock_client = MockODKClient(
                submissions_df=df, metadata={}, etag="test-etag"
            )
            ds = DataStore(
                odk_client=mock_client, refresh_hours=0, cache_path=cache_path
            )

            # Load data (should save to cache)
            ds._load()

            # Verify cache files exist
            assert cache_path.exists()
            assert cache_path.with_suffix(".etag").exists()

            # Create new DataStore and verify cache is loaded
            mock_client2 = MockODKClient(
                submissions_df=df, metadata={}, etag="test-etag"
            )
            ds2 = DataStore(
                odk_client=mock_client2, refresh_hours=0, cache_path=cache_path
            )

            cached = ds2._load_cache()
            assert cached is not None
            cached_df, cached_etags = cached
            assert len(cached_df) == 2
            assert cached_etags == {"bewell_questionnaire": "test-etag"}

    def test_startup_from_cache_populates_whitelists(self):
        """Startup loading from cache must populate whitelists, not just the DataFrame.

        Regression test: previously only a live ODK refresh called
        _extract_whitelists/_compute_observed_periods, so a cache-only startup
        left numerical/categorical whitelists empty and /dimensions returned nothing.
        """
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "wave": [1, 1],
                "school": ["School A", "School A"],
                "sex": ["M", "F"],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.parquet"

            mock_client = MockODKClient(
                submissions_df=df, metadata={}, etag="test-etag"
            )
            ds = DataStore(
                odk_client=mock_client, refresh_hours=0, cache_path=cache_path
            )
            ds._load()  # populates cache on disk

            # Simulate a fresh process starting up with ODK unreachable: cache
            # loads, but the scheduled refresh job fails, so only cache data is used.
            class ErrorODKClient(MockODKClient):
                def fetch_submissions(self, etags=None):
                    raise RuntimeError("ODK Central connection failed")

            ds2 = DataStore(
                odk_client=ErrorODKClient(), refresh_hours=0, cache_path=cache_path
            )
            ds2.startup()

            assert not ds2._df.empty
            assert ds2._numerical_whitelist, (
                "numerical whitelist empty after cache load"
            )
            assert ds2._categorical_whitelist, (
                "categorical whitelist empty after cache load"
            )
            assert ds2._observed_periods, "observed periods empty after cache load"

    def test_load_from_multiple_forms(self):
        """Test loading and materializing submissions across multiple forms."""
        bewell_df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-01T10:00:00Z"],
                "__version": ["2"],
                "bw_wbeing_1": [3],
            }
        )
        phq_df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-02T10:00:00Z"],
                "__version": ["1"],
                "phq9_1": [2],
            }
        )
        demographics_df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-03T10:00:00Z"],
                "yearGroup": [9],
                "d_age": [14],
                "d_sex": ["F"],
            }
        )
        metadata = {
            "_forms": {
                "bewell_questionnaire": {
                    "2": {"variables": {"bw_wbeing_1": {"min": 0, "max": 5}}},
                },
                "phq9_questionnaire": {
                    "1": {"variables": {"phq9_1": {"min": 0, "max": 3}}},
                },
            },
            "_current_versions": {
                "bewell_questionnaire": "2",
                "phq9_questionnaire": "1",
            },
        }

        mock_client = MockODKClient(
            submissions_by_form={
                "bewell_questionnaire": bewell_df,
                "phq9_questionnaire": phq_df,
                "demographics_questionnaire": demographics_df,
            },
            metadata=metadata,
            response_etags={
                "bewell_questionnaire": "etag-bw",
                "phq9_questionnaire": "etag-phq",
                "demographics_questionnaire": "etag-demo",
            },
        )
        ds = DataStore(odk_client=mock_client, refresh_hours=0)

        loaded_df = ds._load()

        assert len(loaded_df) == 1
        assert "bewell_questionnaire__bw_wbeing_1" in loaded_df.columns
        assert "phq9_questionnaire__phq9_1" in loaded_df.columns
        assert "bewell_questionnaire__version" in loaded_df.columns
        assert "phq9_questionnaire__version" in loaded_df.columns
        assert loaded_df.loc[0, "bewell_questionnaire__bw_wbeing_1"] == 3
        assert loaded_df.loc[0, "phq9_questionnaire__phq9_1"] == 2
        assert loaded_df.loc[0, "bewell_questionnaire__version"] == "2"
        assert loaded_df.loc[0, "yearGroup"] == 9
        assert loaded_df.loc[0, "d_age"] == 14
        assert ds._response_etags == {
            "bewell_questionnaire": "etag-bw",
            "phq9_questionnaire": "etag-phq",
            "demographics_questionnaire": "etag-demo",
        }
        assert ds._metadata["_current_versions"] == metadata["_current_versions"]
        assert ds._metadata["bw_wbeing_1"] == {"min": 0, "max": 5}
        assert ds._metadata["phq9_1"] == {"min": 0, "max": 3}

    def test_partial_etag_refresh_reuses_cached_unchanged_forms(self):
        """Test that unchanged forms are reused from cached data."""
        bewell_df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-01T10:00:00Z"],
                "bw_wbeing_1": [3],
            }
        )
        phq_df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-02T10:00:00Z"],
                "phq9_1": [1],
            }
        )
        mock_client = MockODKClient(
            submissions_by_form={
                "bewell_questionnaire": bewell_df,
                "phq9_questionnaire": phq_df,
            },
            metadata={},
            response_etags={
                "bewell_questionnaire": "etag-bw-1",
                "phq9_questionnaire": "etag-phq-1",
            },
        )
        ds = DataStore(odk_client=mock_client, refresh_hours=0)
        first = ds._load()
        ds._df = first

        # Only PHQ changes on second load.
        mock_client.submissions_by_form["phq9_questionnaire"] = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2024-01-04T10:00:00Z"],
                "phq9_1": [3],
            }
        )
        mock_client.response_etags["phq9_questionnaire"] = "etag-phq-2"

        second = ds._load()
        assert len(second) == 1
        assert second.iloc[0]["bewell_questionnaire__bw_wbeing_1"] == 3
        assert second.iloc[0]["phq9_questionnaire__phq9_1"] == 3

    def test_materialize_uses_demographics_authority(self):
        """Test that demographics form is authoritative for yearGroup and d_* fields."""
        stacked = pd.DataFrame(
            {
                "uid": ["S001", "S001", "S001"],
                "school": ["School A", "School A", "School A"],
                "createdAt": [
                    "2024-01-01T10:00:00Z",
                    "2024-01-02T10:00:00Z",
                    "2024-01-03T10:00:00Z",
                ],
                "period_id": ["2023-2024", "2023-2024", "2023-2024"],
                "__xmlFormId": [
                    "bewell_questionnaire",
                    "phq9_questionnaire",
                    "demographics_questionnaire",
                ],
                "bw_wbeing_1": [3, None, None],
                "phq9_1": [None, 2, None],
                "yearGroup": [7, 7, 9],
                "d_age": [12, 12, 14],
                "d_sex": ["M", "M", "F"],
            }
        )
        ds = DataStore.__new__(DataStore)
        materialized = ds._materialize_analytic_rows(stacked)

        assert len(materialized) == 1
        assert materialized.loc[0, "yearGroup"] == 9
        assert materialized.loc[0, "d_age"] == 14
        assert materialized.loc[0, "d_sex"] == "F"
        assert materialized.loc[0, "bewell_questionnaire__bw_wbeing_1"] == 3
        assert materialized.loc[0, "phq9_questionnaire__phq9_1"] == 2

    def test_collapse_latest_non_null_picks_latest_competing_value(self):
        """Test that a column with multiple non-null candidates in a group
        takes the value from the most recent createdAt, and a column that
        doesn't exist in the input is filled with None."""
        df = pd.DataFrame(
            {
                "uid": ["a", "a", "a", "b"],
                "createdAt": pd.to_datetime(
                    [
                        "2024-01-01T10:00:00Z",  # a, earliest
                        "2024-01-03T10:00:00Z",  # a, latest
                        "2024-01-02T10:00:00Z",  # a, middle
                        "2024-01-01T10:00:00Z",  # b, only row
                    ],
                    utc=True,
                ),
                "x": [1, None, 5, 9],
                "y": [None, "late", "mid", "solo"],
            }
        )

        collapsed = DataStore._collapse_latest_non_null(
            df, group_keys=["uid"], value_columns=["x", "y", "missing_col"]
        )
        collapsed = collapsed.set_index("uid")

        # x: latest row (a) has x=None, so the next-latest non-null (5) wins.
        assert collapsed.loc["a", "x"] == 5
        # y: latest row (a) already has a non-null value ("late"), so it wins.
        assert collapsed.loc["a", "y"] == "late"
        assert collapsed.loc["b", "x"] == 9
        assert collapsed.loc["b", "y"] == "solo"
        assert collapsed.loc["a", "missing_col"] is None
        assert collapsed.loc["b", "missing_col"] is None


class TestDataStoreIntegration:
    """Integration tests using the sample_df fixture from conftest."""

    def test_sample_df_has_derived_scores(self, sample_df):
        """Test that the sample_df fixture includes computed derived scores."""
        assert "bw_wbeing_total" in sample_df.columns

        # Verify a few totals manually
        # First row: S001, wave 1, bw_wbeing values: 3, 4, 2
        first_row = sample_df[
            (sample_df["uid"] == "S001") & (sample_df["wave"] == 1)
        ].iloc[0]
        assert first_row["bw_wbeing_total"] == 9  # 3 + 4 + 2

    def test_multiple_waves_preserved(self, sample_df):
        """Test that derived scores work correctly across multiple waves."""
        # Get both waves for student S001
        s001_data = sample_df[sample_df["uid"] == "S001"].sort_values("wave")

        assert len(s001_data) == 2

        # Wave 1: bw_wbeing values are 3, 4, 2
        assert s001_data.iloc[0]["bw_wbeing_total"] == 9  # 3 + 4 + 2

        # Wave 2: bw_wbeing values are 4, 3, 3
        assert s001_data.iloc[1]["bw_wbeing_total"] == 10  # 4 + 3 + 3


class TestPeriodDerivation:
    """Test period derivation from submission timestamps (Step 2 TDD)."""

    def test_derive_period_from_created_at(self):
        """Test that period_id is derived from createdAt timestamp."""
        from glow_api.normalization import derive_period_id
        from datetime import datetime

        # Test with a specific timestamp
        # Assuming UK timezone (Europe/London) and period cutoff at start of academic year
        timestamp = datetime(2023, 10, 15, 10, 30, 0)  # October 15, 2023

        period_id = derive_period_id(timestamp)

        # Period should be "2023-2024" for October 2023
        assert period_id == "2023-2024"

    def test_derive_period_respects_timezone(self):
        """Test that period derivation converts to deployment timezone first."""
        from glow_api.normalization import derive_period_id
        from datetime import datetime, timezone

        # Create UTC timestamp that is Aug 31 in UTC but still Aug 31 in UK
        # UK is GMT+1 in summer (BST), so we need to account for that
        timestamp_utc = datetime(2023, 8, 31, 10, 30, 0, tzinfo=timezone.utc)

        period_id = derive_period_id(timestamp_utc)

        # Should use UK time (Aug 31 11:30 BST), which is before Sept 1
        # August should be in previous academic year
        assert period_id == "2022-2023"

    def test_derive_period_with_cutoff_date(self):
        """Test that period cutoff applies correctly at academic year boundary."""
        from glow_api.normalization import derive_period_id
        from datetime import datetime

        # Just before cutoff (end of August)
        before_cutoff = datetime(2023, 8, 31, 12, 0, 0)
        period_before = derive_period_id(before_cutoff)
        assert period_before == "2022-2023"

        # Just after cutoff (start of September)
        after_cutoff = datetime(2023, 9, 1, 12, 0, 0)
        period_after = derive_period_id(after_cutoff)
        assert period_after == "2023-2024"

    def test_normalize_submissions_adds_period_id(self):
        """Test that normalizing submissions adds period_id column."""
        from glow_api.normalization import normalize_submissions

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "createdAt": ["2023-10-15T10:30:00Z", "2024-02-20T14:00:00Z"],
                "bw_wbeing_1": [3, 4],
            }
        )

        normalized = normalize_submissions(df)

        assert "period_id" in normalized.columns
        assert normalized.loc[0, "period_id"] == "2023-2024"
        assert normalized.loc[1, "period_id"] == "2023-2024"

    def test_normalize_submissions_preserves_original_data(self):
        """Test that normalization preserves all original columns."""
        from glow_api.normalization import normalize_submissions

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2023-10-15T10:30:00Z"],
                "bw_wbeing_1": [3],
                "d_sex": ["M"],
            }
        )

        normalized = normalize_submissions(df)

        # All original columns should still exist
        assert "uid" in normalized.columns
        assert "school" in normalized.columns
        assert "createdAt" in normalized.columns
        assert "bw_wbeing_1" in normalized.columns
        assert "d_sex" in normalized.columns

        # Original values should be unchanged
        assert normalized.loc[0, "uid"] == "S001"
        assert normalized.loc[0, "bw_wbeing_1"] == 3


class TestObservedPeriods:
    """Test observed period discovery (Step 2 TDD)."""

    def test_get_observed_periods_from_data(self):
        """Test extracting observed periods from normalized data."""
        from glow_api.normalization import get_observed_periods

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003", "S004"],
                "period_id": ["2022-2023", "2023-2024", "2023-2024", "2024-2025"],
                "school": ["School A", "School A", "School B", "School A"],
            }
        )

        observed = get_observed_periods(df)

        # Should return chronologically ordered list of unique periods
        assert observed == ["2022-2023", "2023-2024", "2024-2025"]

    def test_get_observed_periods_school_scoped(self):
        """Test observed periods scoped to a specific school."""
        from glow_api.normalization import get_observed_periods

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003", "S004"],
                "period_id": ["2022-2023", "2023-2024", "2023-2024", "2024-2025"],
                "school": ["School A", "School A", "School B", "School B"],
            }
        )

        # School A should only have two periods
        observed_a = get_observed_periods(df, school_name="School A")
        assert observed_a == ["2022-2023", "2023-2024"]

        # School B should only have two different periods
        observed_b = get_observed_periods(df, school_name="School B")
        assert observed_b == ["2023-2024", "2024-2025"]

    def test_observed_periods_empty_data(self):
        """Test observed periods with empty DataFrame."""
        from glow_api.normalization import get_observed_periods

        df = pd.DataFrame(
            {
                "uid": [],
                "period_id": [],
                "school": [],
            }
        )

        observed = get_observed_periods(df)
        assert observed == []


class TestEditedSubmissionPeriodAnchor:
    """Test that edited submissions retain their original period (Step 2 TDD)."""

    def test_edited_submission_keeps_original_period(self):
        """Test that updating a submission doesn't change its period."""
        from glow_api.normalization import normalize_submissions

        # Simulate an edited submission - same uid but different timestamps
        df = pd.DataFrame(
            {
                "uid": ["S001", "S001"],
                "school": ["School A", "School A"],
                "createdAt": [
                    "2023-10-15T10:30:00Z",
                    "2023-10-15T10:30:00Z",
                ],  # Original creation time preserved
                "updatedAt": [
                    "2023-10-15T10:30:00Z",
                    "2024-01-20T14:00:00Z",
                ],  # Later edit
                "bw_wbeing_1": [3, 4],  # Value changed
            }
        )

        normalized = normalize_submissions(df)

        # Both rows should have same period_id based on createdAt
        assert normalized.loc[0, "period_id"] == "2023-2024"
        assert normalized.loc[1, "period_id"] == "2023-2024"


class TestSubmissionMetadataPreservation:
    """Test that submission metadata is preserved for version comparison (Step 2 TDD)."""

    def test_normalize_preserves_version_metadata(self):
        """Test that form version info is preserved."""
        from glow_api.normalization import normalize_submissions

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "createdAt": ["2023-10-15T10:30:00Z", "2024-02-20T14:00:00Z"],
                "__version": ["v1", "v2"],  # ODK form version
                "bw_wbeing_1": [3, 4],
            }
        )

        normalized = normalize_submissions(df)

        # Version metadata should be preserved
        assert "__version" in normalized.columns
        assert normalized.loc[0, "__version"] == "v1"
        assert normalized.loc[1, "__version"] == "v2"

    def test_normalize_preserves_all_system_fields(self):
        """Test that ODK system fields are preserved."""
        from glow_api.normalization import normalize_submissions

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "createdAt": ["2023-10-15T10:30:00Z"],
                "updatedAt": ["2023-10-15T10:30:00Z"],
                "__id": ["uuid-123"],
                "__version": ["v1"],
                "__system/submissionDate": ["2023-10-15T10:30:00Z"],
                "bw_wbeing_1": [3],
            }
        )

        normalized = normalize_submissions(df)

        # All system fields should be preserved
        assert "__id" in normalized.columns
        assert "__version" in normalized.columns
        assert "__system/submissionDate" in normalized.columns


class TestFormVersionCompatibility:
    """Test form version compatibility logic (Step 5 TDD)."""

    def test_compatible_unchanged_limits(self):
        """Test that identical form versions are compatible."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}
        v2_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}

        result = check_version_compatibility("bw_wbeing_1", v1_metadata, v2_metadata)

        assert result["compatible"] is True
        assert result["rescale_needed"] is False

    def test_compatible_narrower_to_wider(self):
        """Test that narrower range expanding to wider is compatible with rescaling."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_metadata = {"bw_wbeing_1": {"min": 0, "max": 3}}
        v2_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}

        result = check_version_compatibility("bw_wbeing_1", v1_metadata, v2_metadata)

        assert result["compatible"] is True
        assert result["rescale_needed"] is True
        assert result["rescale_from"] == (0, 3)
        assert result["rescale_to"] == (0, 5)

    def test_compatible_limit_shift(self):
        """Test that shifted limits (e.g., 0-indexed to 1-indexed) are compatible."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}
        v2_metadata = {"bw_wbeing_1": {"min": 1, "max": 6}}

        result = check_version_compatibility("bw_wbeing_1", v1_metadata, v2_metadata)

        # Shifted ranges are compatible with rescaling
        assert result["compatible"] is True
        assert result["rescale_needed"] is True

    def test_incompatible_different_scales(self):
        """Test that incompatible scales (can't be linearly mapped) are flagged."""
        from glow_api.version_compatibility import check_version_compatibility

        # Very different scales that can't be safely rescaled
        v1_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}
        v2_metadata = {"bw_wbeing_1": {"min": 0, "max": 10}}

        result = check_version_compatibility("bw_wbeing_1", v1_metadata, v2_metadata)

        # For now, allow this as rescalable
        # In future, might want stricter rules
        assert result["compatible"] is True
        assert result["rescale_needed"] is True

    def test_incompatible_missing_in_new_version(self):
        """Test that variable removed in new version is incompatible."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_metadata = {"bw_wbeing_1": {"min": 0, "max": 5}}
        v2_metadata = {}  # Variable removed

        result = check_version_compatibility("bw_wbeing_1", v1_metadata, v2_metadata)

        assert result["compatible"] is False
        assert result["reason"] == "variable-not-in-new-version"

    def test_rescale_values(self):
        """Test linear rescaling of values."""
        from glow_api.version_compatibility import rescale_value

        # Rescale from 0-3 to 0-5
        assert rescale_value(0, from_range=(0, 3), to_range=(0, 5)) == 0.0
        assert rescale_value(3, from_range=(0, 3), to_range=(0, 5)) == 5.0
        assert rescale_value(1.5, from_range=(0, 3), to_range=(0, 5)) == 2.5

    def test_apply_rescaling_to_period(self):
        """Test applying rescaling to all values in a period."""
        from glow_api.version_compatibility import apply_rescaling
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "bw_wbeing_1": [0, 1.5, 3],
                "__version": ["v1", "v1", "v1"],
            }
        )

        rescaled = apply_rescaling(
            df,
            variable="bw_wbeing_1",
            from_range=(0, 3),
            to_range=(0, 5),
        )

        # Values should be rescaled
        assert rescaled.loc[0, "bw_wbeing_1"] == 0.0
        assert rescaled.loc[1, "bw_wbeing_1"] == 2.5
        assert rescaled.loc[2, "bw_wbeing_1"] == 5.0
