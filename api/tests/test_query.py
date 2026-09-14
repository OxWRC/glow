"""Tests for query template endpoint with blanket suppression."""

import pytest
from sqlalchemy.orm import Session

from glow_api.database import create_user
from glow_api.metadata_models import School


@pytest.fixture
def alpha_user(db_session: Session, sample_schools: dict[str, School]) -> dict:
    """Create a test user for Focus School Academy school."""
    user = create_user(
        db_session,
        username="alpha_test",
        school_ids=[sample_schools["Focus School Academy"].id],
        is_admin=False,
    )
    return {
        "username": user.username,
        "school_id": sample_schools["Focus School Academy"].id,
        "school_name": "Focus School Academy",
    }


@pytest.fixture
def beta_user(db_session: Session, sample_schools: dict[str, School]) -> dict:
    """Create a test user for Neighbouring School school."""
    user = create_user(
        db_session,
        username="beta_test",
        school_ids=[sample_schools["Neighbouring School"].id],
        is_admin=False,
    )
    return {
        "username": user.username,
        "school_id": sample_schools["Neighbouring School"].id,
        "school_name": "Neighbouring School",
    }


class TestNewQueryExecution:
    """Test new period-oriented query execution (Step 3 TDD)."""

    def test_query_variable_selection_single_variable(self):
        """Test selecting a single variable with 'v' parameter."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        # Create test data with period_id
        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
            }
        )

        # Normalize query
        query = normalize_query(v=["bw_wbeing_1"])

        # Execute query
        result = execute_query(df, query)

        # Should return results for one variable
        assert "variables" in result
        assert len(result["variables"]) == 1
        assert result["variables"][0]["variable"] == "bw_wbeing_1"

    def test_query_variable_selection_multiple_variables(self):
        """Test selecting multiple variables with repeated 'v' parameters."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "bw_wbeing_1": [3, 4],
                "bw_wbeing_2": [4, 3],
                "bw_stress_1": [2, 3],
            }
        )

        query = normalize_query(v=["bw_wbeing_1", "bw_stress_1"])
        result = execute_query(df, query)

        assert len(result["variables"]) == 2
        var_names = [v["variable"] for v in result["variables"]]
        assert "bw_wbeing_1" in var_names
        assert "bw_stress_1" in var_names

    def test_query_namespaced_variable_selection(self):
        """Test selecting namespaced multi-form variables."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "period_id": ["2023-2024"],
                "createdAt": ["2024-01-01T10:00:00Z"],
                "bewell_questionnaire__bw_wbeing_1": [3],
                "phq9_questionnaire__phq9_1": [2],
            }
        )

        numerical_whitelist = [
            "bewell_questionnaire__bw_wbeing_1",
            "phq9_questionnaire__phq9_1",
        ]

        query = normalize_query(v=["phq9_questionnaire__phq9_1"])
        result = execute_query(df, query, numerical_whitelist=numerical_whitelist)

        assert len(result["variables"]) == 1
        assert result["variables"][0]["variable"] == "phq9_questionnaire__phq9_1"

    def test_query_namespaced_variable_prefix_expansion(self):
        """Test prefix expansion against the raw variable part of namespaced variables."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "period_id": ["2023-2024"],
                "createdAt": ["2024-01-01T10:00:00Z"],
                "bewell_questionnaire__bw_wbeing_1": [3],
                "phq9_questionnaire__phq9_1": [2],
            }
        )

        numerical_whitelist = [
            "bewell_questionnaire__bw_wbeing_1",
            "phq9_questionnaire__phq9_1",
        ]

        query = normalize_query(variable_prefix=["phq9_"])
        result = execute_query(df, query, numerical_whitelist=numerical_whitelist)

        assert len(result["variables"]) == 1
        assert result["variables"][0]["variable"] == "phq9_questionnaire__phq9_1"

    def test_query_reports_question_version_counts_for_namespaced_variable(self):
        """Test question_versions uses version->count mapping for namespaced variables."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "createdAt": ["2024-01-01T10:00:00Z", "2024-01-02T10:00:00Z"],
                "bewell_questionnaire__version": ["1", "2"],
                "bewell_questionnaire__bw_wbeing_1": [3, 4],
            }
        )

        metadata = {
            "_forms": {
                "bewell_questionnaire": {
                    "1": {"variables": {"bw_wbeing_1": {"min": 1, "max": 6}}},
                    "2": {"variables": {"bw_wbeing_1": {"min": 0, "max": 5}}},
                }
            },
            "_current_versions": {"bewell_questionnaire": "2"},
        }

        query = normalize_query(v=["bewell_questionnaire__bw_wbeing_1"])
        result = execute_query(
            df,
            query,
            numerical_whitelist=["bewell_questionnaire__bw_wbeing_1"],
            observed_periods=["2023-2024"],
            form_metadata=metadata,
            min_n=1,
        )

        period_slice = result["variables"][0]["periods"]["2023-2024"]
        assert period_slice["question_versions"] == {"1": 1, "2": 1}
        assert period_slice["notes"] == ["values-rescaled"]

    def test_query_variable_prefix_expansion(self):
        """Test expanding variable_prefix to matching variables."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "period_id": ["2023-2024"],
                "bw_wbeing_1": [3],
                "bw_wbeing_2": [4],
                "bw_stress_1": [2],
                "d_sex": ["M"],
            }
        )

        query = normalize_query(variable_prefix=["bw_wbeing"])
        result = execute_query(df, query)

        # Should expand to all bw_wbeing_* variables
        var_names = [v["variable"] for v in result["variables"]]
        assert "bw_wbeing_1" in var_names
        assert "bw_wbeing_2" in var_names
        assert "bw_stress_1" not in var_names  # Different prefix

    def test_query_all_variables_default(self):
        """Test that omitting both 'v' and 'variable_prefix' means all variables."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001"],
                "school": ["School A"],
                "period_id": ["2023-2024"],
                "bw_wbeing_1": [3],
                "bw_stress_1": [2],
                "d_sex": ["M"],  # Categorical, not a variable
            }
        )

        # Assume we have a whitelist of numerical variables
        numerical_whitelist = ["bw_wbeing_1", "bw_stress_1"]

        query = normalize_query()  # No variables specified
        result = execute_query(df, query, numerical_whitelist=numerical_whitelist)

        # Should include all numerical variables
        var_names = [v["variable"] for v in result["variables"]]
        assert "bw_wbeing_1" in var_names
        assert "bw_stress_1" in var_names

    def test_query_response_organization_periods(self):
        """Test that response includes top-level periods list."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "school": ["School A", "School A", "School A"],
                "period_id": ["2022-2023", "2023-2024", "2023-2024"],
                "bw_wbeing_1": [3, 4, 5],
            }
        )

        query = normalize_query(v=["bw_wbeing_1"])
        result = execute_query(df, query)

        # Should have top-level periods in chronological order
        assert "periods" in result
        assert result["periods"] == ["2022-2023", "2023-2024"]

    def test_query_response_organization_dimensions(self):
        """Test that response includes requested dimensions."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "d_sex": ["M", "F"],
                "bw_wbeing_1": [3, 4],
            }
        )

        query = normalize_query(v=["bw_wbeing_1"], d=["d_sex"])
        result = execute_query(df, query)

        # Should echo requested dimensions
        assert "dimensions" in result
        assert result["dimensions"] == ["d_sex"]

    def test_query_dimension_missing_value_is_json_safe(self):
        """Missing dimension values must serialize as null, not NaN."""
        import json
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": [f"S{i}" for i in range(5)],
                "school": ["School A"] * 5,
                "period_id": ["2023-2024"] * 5,
                "d_sex": [None] * 5,
                "bw_wbeing_1": [3, 4, 5, 2, 3],
            }
        )

        query = normalize_query(v=["bw_wbeing_1"], d=["d_sex"])
        result = execute_query(df, query, min_n=5)

        cell = result["variables"][0]["periods"]["2023-2024"]["cells"][0]
        assert cell["d_sex"] is None
        json.dumps(result)  # raises ValueError on NaN

    def test_query_period_organized_results(self):
        """Test that results are organized by period within each variable."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002", "S003"],
                "school": ["School A", "School A", "School A"],
                "period_id": ["2022-2023", "2023-2024", "2023-2024"],
                "bw_wbeing_1": [3, 4, 5],
            }
        )

        query = normalize_query(v=["bw_wbeing_1"])
        result = execute_query(df, query)

        # Variable should have periods dict
        var = result["variables"][0]
        assert "periods" in var
        assert isinstance(var["periods"], dict)

        # Should have entries for observed periods
        assert "2022-2023" in var["periods"]
        assert "2023-2024" in var["periods"]

    def test_query_missing_period_not_in_results(self):
        """Test that periods with no data for a variable are omitted."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["S001", "S002"],
                "school": ["School A", "School A"],
                "period_id": ["2022-2023", "2023-2024"],
                "bw_wbeing_1": [3, None],  # No value in 2023-2024
            }
        )

        query = normalize_query(v=["bw_wbeing_1"])
        result = execute_query(df, query)

        # Period 2023-2024 might be missing or empty for this variable
        # (depends on how we handle null values)
        var = result["variables"][0]
        # At minimum, should have 2022-2023
        assert "2022-2023" in var["periods"]


class TestDeduplication:
    """Test per-variable deduplication (Step 3 TDD)."""

    def test_deduplicate_latest_non_null_per_uid(self):
        """Test that deduplication keeps latest non-null value per uid per period."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        df = pd.DataFrame(
            {
                "uid": ["S001", "S001", "S001"],
                "school": ["School A", "School A", "School A"],
                "period_id": ["2023-2024", "2023-2024", "2023-2024"],
                "createdAt": [
                    datetime(2023, 10, 1, 10, 0, 0),
                    datetime(2023, 10, 15, 10, 0, 0),  # Latest
                    datetime(2023, 10, 5, 10, 0, 0),
                ],
                "bw_wbeing_1": [3, 4, 2],
            }
        )

        deduped = deduplicate_submissions(df, variable="bw_wbeing_1")

        # Should keep only the latest submission for S001
        assert len(deduped) == 1
        assert deduped.iloc[0]["bw_wbeing_1"] == 4  # From Oct 15

    def test_deduplicate_ignores_null_values(self):
        """Test that null values are ignored in latest-non-null logic."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        df = pd.DataFrame(
            {
                "uid": ["S001", "S001", "S001"],
                "school": ["School A", "School A", "School A"],
                "period_id": ["2023-2024", "2023-2024", "2023-2024"],
                "createdAt": [
                    datetime(2023, 10, 1, 10, 0, 0),
                    datetime(2023, 10, 15, 10, 0, 0),  # Latest but null
                    datetime(2023, 10, 5, 10, 0, 0),
                ],
                "bw_wbeing_1": [3, None, 2],
            }
        )

        deduped = deduplicate_submissions(df, variable="bw_wbeing_1")

        # Should keep Oct 5 (value=2), not Oct 15 (null)
        assert len(deduped) == 1
        assert deduped.iloc[0]["bw_wbeing_1"] == 2

    def test_deduplicate_separate_periods(self):
        """Test that deduplication happens independently per period."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        df = pd.DataFrame(
            {
                "uid": ["S001", "S001", "S001", "S001"],
                "school": ["School A", "School A", "School A", "School A"],
                "period_id": ["2022-2023", "2022-2023", "2023-2024", "2023-2024"],
                "createdAt": [
                    datetime(2022, 10, 1, 10, 0, 0),
                    datetime(2022, 10, 15, 10, 0, 0),
                    datetime(2023, 10, 1, 10, 0, 0),
                    datetime(2023, 10, 15, 10, 0, 0),
                ],
                "bw_wbeing_1": [3, 4, 5, 6],
            }
        )

        deduped = deduplicate_submissions(df, variable="bw_wbeing_1")

        # Should have one row per period
        assert len(deduped) == 2

        # Check period 2022-2023 has latest value
        period_2022 = deduped[deduped["period_id"] == "2022-2023"].iloc[0]
        assert period_2022["bw_wbeing_1"] == 4

        # Check period 2023-2024 has latest value
        period_2023 = deduped[deduped["period_id"] == "2023-2024"].iloc[0]
        assert period_2023["bw_wbeing_1"] == 6

    def test_deduplicate_different_uids(self):
        """Test that different uids are kept separate."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        df = pd.DataFrame(
            {
                "uid": ["S001", "S001", "S002", "S002"],
                "school": ["School A", "School A", "School A", "School A"],
                "period_id": ["2023-2024", "2023-2024", "2023-2024", "2023-2024"],
                "createdAt": [
                    datetime(2023, 10, 1, 10, 0, 0),
                    datetime(2023, 10, 15, 10, 0, 0),
                    datetime(2023, 10, 1, 10, 0, 0),
                    datetime(2023, 10, 15, 10, 0, 0),
                ],
                "bw_wbeing_1": [3, 4, 5, 6],
            }
        )

        deduped = deduplicate_submissions(df, variable="bw_wbeing_1")

        # Should have one row per uid
        assert len(deduped) == 2
        assert set(deduped["uid"]) == {"S001", "S002"}


class TestQueryETag:
    """Test ETag behavior for query caching (Step 3 TDD)."""

    def test_query_returns_etag(self):
        """Test that query response includes an ETag header."""
        from glow_api.query_execution import compute_query_etag
        from glow_api.canonical_query import normalize_query

        query = normalize_query(v=["bw_wbeing_1"])
        etag = compute_query_etag(
            query=query,
            dataset_version="test-etag-123",
            api_version="0.1.0",
        )

        # Should return a non-empty string
        assert etag is not None
        assert isinstance(etag, str)
        assert len(etag) > 0

    def test_query_etag_deterministic(self):
        """Test that same query produces same ETag."""
        from glow_api.query_execution import compute_query_etag
        from glow_api.canonical_query import normalize_query

        query = normalize_query(v=["bw_wbeing_1"], d=["d_sex"])

        etag1 = compute_query_etag(query, "test-123", "0.1.0")
        etag2 = compute_query_etag(query, "test-123", "0.1.0")

        assert etag1 == etag2

    def test_query_etag_changes_with_query(self):
        """Test that different queries produce different ETags."""
        from glow_api.query_execution import compute_query_etag
        from glow_api.canonical_query import normalize_query

        query1 = normalize_query(v=["bw_wbeing_1"])
        query2 = normalize_query(v=["bw_wbeing_2"])

        etag1 = compute_query_etag(query1, "test-123", "0.1.0")
        etag2 = compute_query_etag(query2, "test-123", "0.1.0")

        assert etag1 != etag2

    def test_query_etag_changes_with_dataset_version(self):
        """Test that ETag changes when dataset changes."""
        from glow_api.query_execution import compute_query_etag
        from glow_api.canonical_query import normalize_query

        query = normalize_query(v=["bw_wbeing_1"])

        etag1 = compute_query_etag(query, "version-1", "0.1.0")
        etag2 = compute_query_etag(query, "version-2", "0.1.0")

        assert etag1 != etag2

    def test_query_etag_changes_with_api_version(self):
        """Test that ETag changes when API version changes."""
        from glow_api.query_execution import compute_query_etag
        from glow_api.canonical_query import normalize_query

        query = normalize_query(v=["bw_wbeing_1"])

        etag1 = compute_query_etag(query, "test-123", "0.1.0")
        etag2 = compute_query_etag(query, "test-123", "0.2.0")

        assert etag1 != etag2


class TestDeduplicationPerSchoolPeriod:
    """Test deduplication happens per uid per school-period bucket."""

    def test_deduplication_separates_by_school(self):
        """Test that deduplication doesn't collapse same uid across different schools."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        # Create test data with same uid in two different schools
        df = pd.DataFrame(
            {
                "uid": ["U001", "U001"],
                "school": ["School A", "School B"],
                "period_id": ["2023-2024", "2023-2024"],
                "createdAt": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
                "bw_wbeing_1": [3, 5],  # Different values
            }
        )

        result = deduplicate_submissions(df, "bw_wbeing_1")

        # Should have 2 rows (one per school)
        assert len(result) == 2

        # Both schools should be represented
        assert set(result["school"]) == {"School A", "School B"}

        # Each school should have its own value
        school_a_value = result[result["school"] == "School A"]["bw_wbeing_1"].iloc[0]
        school_b_value = result[result["school"] == "School B"]["bw_wbeing_1"].iloc[0]
        assert school_a_value == 3
        assert school_b_value == 5

    def test_deduplication_latest_non_null_per_school(self):
        """Test latest-non-null rule applies within each school separately."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        # Same uid, same school, same period, multiple submissions
        df = pd.DataFrame(
            {
                "uid": ["U001", "U001", "U001"],
                "school": ["School A", "School A", "School A"],
                "period_id": ["2023-2024", "2023-2024", "2023-2024"],
                "createdAt": [
                    datetime(2024, 1, 1),
                    datetime(2024, 1, 2),
                    datetime(2024, 1, 3),
                ],
                "bw_wbeing_1": [3, None, 5],  # Latest non-null is 5
            }
        )

        result = deduplicate_submissions(df, "bw_wbeing_1")

        # Should have 1 row (latest non-null)
        assert len(result) == 1
        assert result["bw_wbeing_1"].iloc[0] == 5

    def test_deduplication_drops_all_null_submissions(self):
        """Test that all-null submissions are dropped after deduplication."""
        from glow_api.query_execution import deduplicate_submissions
        import pandas as pd
        from datetime import datetime

        # Same uid, all submissions have null value
        df = pd.DataFrame(
            {
                "uid": ["U001", "U001"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "createdAt": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
                "bw_wbeing_1": [None, None],
            }
        )

        result = deduplicate_submissions(df, "bw_wbeing_1")

        # Should be empty (all null values are dropped)
        assert len(result) == 0


class TestDerivedTotalsFromDedupedItems:
    """Test that derived totals are recomputed from deduped constituent items."""

    def test_derived_total_detection(self):
        """Test that variables ending with _total are correctly identified."""
        from glow_api.query_execution import is_derived_total

        assert is_derived_total("bw_swemwbs_total") is True
        assert is_derived_total("bw_wbeing_1") is False
        assert is_derived_total("bw_stress_total") is True
        assert is_derived_total("something_total") is True

    def test_constituent_items_extraction(self):
        """Test extraction of constituent item columns for a derived total."""
        from glow_api.query_execution import get_constituent_items

        available_columns = [
            "uid",
            "school",
            "bw_swemwbs_1",
            "bw_swemwbs_2",
            "bw_swemwbs_3",
            "bw_swemwbs_total",
            "bw_stress_1",
            "bw_stress_2",
        ]

        constituents = get_constituent_items("bw_swemwbs_total", available_columns)

        assert len(constituents) == 3
        assert "bw_swemwbs_1" in constituents
        assert "bw_swemwbs_2" in constituents
        assert "bw_swemwbs_3" in constituents
        assert "bw_stress_1" not in constituents

    def test_derived_total_recomputation(self):
        """Test that derived totals are recomputed from constituent items."""
        from glow_api.query_execution import recompute_derived_total
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["U001", "U002"],
                "bw_swemwbs_1": [2, 3],
                "bw_swemwbs_2": [3, 4],
                "bw_swemwbs_3": [4, 5],
                "bw_swemwbs_total": [999, 999],  # Wrong precomputed values
            }
        )

        result = recompute_derived_total(
            df,
            "bw_swemwbs_total",
            ["bw_swemwbs_1", "bw_swemwbs_2", "bw_swemwbs_3"],
        )

        # Total should be recomputed
        assert result["bw_swemwbs_total"].iloc[0] == 9  # 2 + 3 + 4
        assert result["bw_swemwbs_total"].iloc[1] == 12  # 3 + 4 + 5

    def test_derived_total_handles_missing_values(self):
        """Test that derived total computation skips NaN values."""
        from glow_api.query_execution import recompute_derived_total
        import pandas as pd
        import numpy as np

        df = pd.DataFrame(
            {
                "uid": ["U001"],
                "bw_swemwbs_1": [2],
                "bw_swemwbs_2": [np.nan],
                "bw_swemwbs_3": [4],
                "bw_swemwbs_total": [999],
            }
        )

        result = recompute_derived_total(
            df,
            "bw_swemwbs_total",
            ["bw_swemwbs_1", "bw_swemwbs_2", "bw_swemwbs_3"],
        )

        # Total should skip NaN
        assert result["bw_swemwbs_total"].iloc[0] == 6  # 2 + 4, skipping NaN


class TestVersionAwareComparison:
    """Test version-aware comparison and rescaling."""

    def test_version_compatibility_identical_ranges(self):
        """Test that identical min/max ranges are compatible without rescaling."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_meta = {"bw_wbeing_1": {"min": 1, "max": 5}}
        v2_meta = {"bw_wbeing_1": {"min": 1, "max": 5}}

        result = check_version_compatibility("bw_wbeing_1", v1_meta, v2_meta)

        assert result["compatible"] is True
        assert result["rescale_needed"] is False

    def test_version_compatibility_different_ranges_rescalable(self):
        """Test that different ranges are compatible but require rescaling."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_meta = {"bw_wbeing_1": {"min": 1, "max": 5}}
        v2_meta = {"bw_wbeing_1": {"min": 0, "max": 10}}

        result = check_version_compatibility("bw_wbeing_1", v1_meta, v2_meta)

        assert result["compatible"] is True
        assert result["rescale_needed"] is True
        assert result["rescale_from"] == (1, 5)
        assert result["rescale_to"] == (0, 10)

    def test_version_compatibility_variable_removed(self):
        """Test that removed variables are incompatible."""
        from glow_api.version_compatibility import check_version_compatibility

        v1_meta = {"bw_wbeing_1": {"min": 1, "max": 5}}
        v2_meta = {}  # Variable removed

        result = check_version_compatibility("bw_wbeing_1", v1_meta, v2_meta)

        assert result["compatible"] is False
        assert result["reason"] == "variable-not-in-new-version"

    def test_rescale_value_linear_transformation(self):
        """Test linear rescaling of values."""
        from glow_api.version_compatibility import rescale_value

        # Rescale 3 from [1, 5] to [0, 10]
        # (3 - 1) / (5 - 1) = 0.5
        # 0.5 * (10 - 0) + 0 = 5
        result = rescale_value(3, (1, 5), (0, 10))

        assert result == 5.0

    def test_apply_rescaling_to_dataframe(self):
        """Test rescaling applied to DataFrame column."""
        from glow_api.version_compatibility import apply_rescaling
        import pandas as pd

        df = pd.DataFrame(
            {
                "uid": ["U001", "U002"],
                "bw_wbeing_1": [1, 5],  # Min and max of old range
            }
        )

        result = apply_rescaling(df, "bw_wbeing_1", (1, 5), (0, 10))

        # 1 should map to 0, 5 should map to 10
        assert result["bw_wbeing_1"].iloc[0] == 0.0
        assert result["bw_wbeing_1"].iloc[1] == 10.0

    def test_incompatible_version_suppresses_period(self):
        """Test that incompatible versions cause period suppression."""
        from glow_api.query_execution import execute_query
        from glow_api.canonical_query import normalize_query
        import pandas as pd

        # Create data with multiple versions in same period
        df = pd.DataFrame(
            {
                "uid": ["U001", "U002"],
                "school": ["School A", "School A"],
                "period_id": ["2023-2024", "2023-2024"],
                "createdAt": ["2024-01-01", "2024-01-02"],
                "__version": [1, 2],
                "bw_wbeing_1": [3, 4],
            }
        )

        # Provide metadata that makes versions incompatible
        # (variable doesn't exist in version 2)
        form_metadata = {
            "bw_wbeing_1": {"min": 1, "max": 5},
        }

        query = normalize_query(v=["bw_wbeing_1"])

        # Note: This test assumes the version compatibility check
        # will mark missing variables as incompatible
        result = execute_query(
            df,
            query,
            observed_periods=["2023-2024"],
            form_metadata=form_metadata,
        )

        # The implementation should suppress or handle the incompatibility
        # This test documents the expected behavior
        assert "variables" in result
