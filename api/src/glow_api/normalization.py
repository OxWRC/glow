"""Submission normalization layer.

This module derives periods from timestamps, preserves metadata,
and prepares submissions for query execution.
"""

import logging
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from glow_api.settings import settings

logger = logging.getLogger(__name__)


def normalize_submissions(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize submissions by deriving periods and preserving metadata.

    Args:
        df: Raw submissions DataFrame with createdAt timestamps

    Returns:
        Normalized DataFrame with period_id column added

    Rules:
        - Derives period_id from createdAt timestamp
        - Preserves all original columns
        - Preserves ODK system fields (__version, __id, etc.)
    """
    if df.empty:
        # Add period_id column to empty DataFrame
        df_copy = df.copy()
        df_copy["period_id"] = pd.Series(dtype=str)
        return df_copy

    # Make a copy to avoid modifying original
    df_normalized = df.copy()

    # ODK CSV export names the timestamp column SubmissionDate. Normalize it to createdAt.
    if (
        "createdAt" not in df_normalized.columns
        and "SubmissionDate" in df_normalized.columns
    ):
        # Naive timestamps from data are interpreted as UTC (utc=True below).
        df_normalized["createdAt"] = df_normalized["SubmissionDate"]

    # Parse createdAt timestamps and derive period_id
    if "createdAt" in df_normalized.columns:
        # Convert to datetime if not already
        df_normalized["createdAt_parsed"] = pd.to_datetime(
            df_normalized["createdAt"], utc=True
        )

        # Derive period_id for all rows at once: convert to deployment
        # timezone, then compare month/day/year against the cutoff.
        local = df_normalized["createdAt_parsed"].dt.tz_convert(
            ZoneInfo(settings.PERIOD_TIMEZONE)
        )
        cutoff_month = settings.PERIOD_CUTOFF_MONTH
        cutoff_day = settings.PERIOD_CUTOFF_DAY
        before_cutoff = (local.dt.month < cutoff_month) | (
            (local.dt.month == cutoff_month) & (local.dt.day < cutoff_day)
        )
        start_year = local.dt.year.where(~before_cutoff, local.dt.year - 1)
        df_normalized["period_id"] = (
            start_year.astype("Int64").astype(str)
            + "-"
            + (start_year + 1).astype("Int64").astype(str)
        )

        # Drop the temporary parsed column
        df_normalized = df_normalized.drop(columns=["createdAt_parsed"])
    else:
        # If no createdAt column, add empty period_id column
        logger.warning(
            "No createdAt column found in submissions - cannot derive periods"
        )
        df_normalized["period_id"] = None

    return df_normalized


def get_observed_periods(
    df: pd.DataFrame, school_name: Optional[str] = None
) -> list[str]:
    """Get chronologically ordered list of observed periods.

    Args:
        df: Normalized DataFrame with period_id column
        school_name: Optional school name to scope the periods to

    Returns:
        Sorted list of unique period_id values

    Rules:
        - Returns only periods that have at least one submission
        - Ordered chronologically (earliest first)
        - If school_name is provided, returns only periods for that school
    """
    if df.empty:
        return []

    # Filter by school if requested
    if school_name is not None:
        if "school" in df.columns:
            df = df[df["school"] == school_name]
        else:
            logger.warning(
                f"No 'school' column found - cannot filter by school_name={school_name}"
            )
            return []

    # Get unique period_ids
    if "period_id" not in df.columns:
        logger.warning("No period_id column found in DataFrame")
        return []

    # Drop NaN values and get unique periods
    periods = df["period_id"].dropna().unique().tolist()

    # Sort chronologically
    # Period IDs are in format "YYYY-YYYY", so alphabetical sort works
    periods.sort()

    return periods
