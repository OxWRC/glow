"""floored_period_counts: the shared helper behind /query/counts and
/export/counts - must never reveal an exact count below min_n."""

import pandas as pd

from glow_api.raw_counts import floored_period_counts


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_period_at_or_above_min_n_returns_exact_count():
    df = _df([{"uid": f"S{i}", "period_id": "2026-W01"} for i in range(5)])

    assert floored_period_counts(df, min_n=5) == {"2026-W01": 5}


def test_period_below_min_n_returns_none_not_the_number():
    df = _df([{"uid": f"S{i}", "period_id": "2026-W01"} for i in range(4)])

    assert floored_period_counts(df, min_n=5) == {"2026-W01": None}


def test_counts_distinct_students_not_rows():
    # Same student appears twice in one period (e.g. a resubmission) -
    # must not be double-counted into clearing the threshold.
    df = _df(
        [{"uid": "S1", "period_id": "2026-W01"}] * 2
        + [{"uid": f"S{i}", "period_id": "2026-W01"} for i in range(2, 5)]
    )

    assert floored_period_counts(df, min_n=5) == {"2026-W01": None}


def test_each_period_floored_independently():
    df = _df(
        [{"uid": f"S{i}", "period_id": "2026-W01"} for i in range(5)]
        + [{"uid": f"T{i}", "period_id": "2026-W02"} for i in range(2)]
    )

    assert floored_period_counts(df, min_n=5) == {
        "2026-W01": 5,
        "2026-W02": None,
    }


def test_rows_with_no_period_id_are_excluded():
    df = _df(
        [{"uid": f"S{i}", "period_id": "2026-W01"} for i in range(5)]
        + [{"uid": "S99", "period_id": None}]
    )

    assert floored_period_counts(df, min_n=5) == {"2026-W01": 5}


def test_empty_df_returns_empty_dict():
    assert floored_period_counts(pd.DataFrame(), min_n=5) == {}


def test_missing_expected_columns_returns_empty_dict():
    assert floored_period_counts(pd.DataFrame({"foo": [1, 2]}), min_n=5) == {}
