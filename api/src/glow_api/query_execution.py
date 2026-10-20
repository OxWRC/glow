"""Period-oriented query execution engine.

This module replaces the old wave-first, neighbor-oriented query engine
with a period-oriented multi-variable query engine.
"""

import hashlib
import logging
from typing import Optional

import pandas as pd

from glow_api.canonical_query import CanonicalQuery, canonical_query_to_etag_key
from glow_api.normalization import get_observed_periods

logger = logging.getLogger(__name__)


def split_namespaced_variable(variable: str) -> tuple[Optional[str], str]:
    """Split `form__field` into `(form, field)` or return `(None, variable)`."""
    if "__" in variable:
        form_id, field_name = variable.split("__", 1)
        return form_id, field_name
    return None, variable


def get_version_column_name(variable: str) -> str:
    """Return the version column associated with a variable."""
    form_id, _ = split_namespaced_variable(variable)
    if form_id is None:
        return "__version"
    return f"{form_id}__version"


def get_variable_part(variable: str) -> str:
    """Return the raw field name part of a namespaced variable."""
    _, field_name = split_namespaced_variable(variable)
    return field_name


def get_version_metadata_map(
    form_metadata: Optional[dict],
    variable: str,
    version: str,
) -> dict:
    """Get the variable metadata map for one form version.

    For legacy unnamespaced variables, fall back to the flat metadata mapping.
    """
    if not form_metadata:
        return {}

    form_id, _ = split_namespaced_variable(variable)
    if form_id is None:
        return {
            key: value
            for key, value in form_metadata.items()
            if not key.startswith("_")
        }

    return (
        form_metadata.get("_forms", {})
        .get(form_id, {})
        .get(str(version), {})
        .get("variables", {})
    )


def compute_query_etag(
    query: CanonicalQuery,
    dataset_version: str,
    api_version: str,
) -> str:
    """Compute ETag for a query response.

    Args:
        query: Canonical query
        dataset_version: Dataset version marker (ODK ETag or timestamp)
        api_version: API version

    Returns:
        ETag string (quoted hexdigest)

    Rules:
        - ETag incorporates canonical query, dataset version, and API version
        - Same inputs always produce same ETag (deterministic)
        - Different queries or versions produce different ETags
    """
    # Build ETag seed from all components
    query_key = canonical_query_to_etag_key(query)
    etag_seed = f"{query_key}||{dataset_version}||{api_version}"

    # Hash to produce deterministic ETag
    etag_hash = hashlib.sha256(etag_seed.encode()).hexdigest()[:16]

    # Return quoted ETag per HTTP spec
    return f'"{etag_hash}"'


def deduplicate_submissions(
    df: pd.DataFrame,
    variable: str,
) -> pd.DataFrame:
    """Deduplicate submissions using latest-non-null rule.

    Args:
        df: DataFrame with submissions (must have uid, period_id, school, createdAt, and variable)
        variable: Variable name to deduplicate on

    Returns:
        Deduped DataFrame with one row per uid per school-period bucket

    Rules:
        - For each uid in each school-period bucket, keep only the latest non-null value
        - If all values are null for a uid in a bucket, drop that uid entirely for this variable
        - Deduplication happens independently per period and per school
        - This prevents collapsing records from different schools
    """
    if df.empty:
        return df

    # Make sure we have required columns
    required_cols = ["uid", "period_id", "createdAt", variable]
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"Missing column {col} for deduplication")
            return df

    # Determine grouping columns - include school if it exists
    group_cols = ["uid", "period_id"]
    if "school" in df.columns:
        group_cols.append("school")

    # Parse createdAt to UTC-aware
    df = df.copy()
    # Naive timestamps from data are interpreted as UTC.
    df["createdAt"] = pd.to_datetime(df["createdAt"], utc=True, format="ISO8601")

    # Sort by createdAt descending (latest first)
    df = df.sort_values("createdAt", ascending=False)

    # For each uid-school-period group, keep the first non-null value
    # If all are null, drop the group entirely (don't keep null submissions)
    deduped_rows = []

    for group_key, group in df.groupby(group_cols, sort=False):
        # Try to find first non-null value
        non_null = group[group[variable].notna()]

        if not non_null.empty:
            # Keep first non-null (which is latest due to sorting)
            deduped_rows.append(non_null.iloc[0])
        # If all null, drop this group entirely (don't keep null records)

    if not deduped_rows:
        return pd.DataFrame(columns=df.columns)

    return pd.DataFrame(deduped_rows).reset_index(drop=True)


def is_derived_total(variable: str) -> bool:
    """Check if a variable is a derived total.

    Args:
        variable: Variable name to check

    Returns:
        True if the variable is a derived total (ends with '_total')
    """
    return variable.endswith("_total")


def get_constituent_items(variable: str, available_columns: list[str]) -> list[str]:
    """Get the constituent item columns for a derived total variable.

    Args:
        variable: Derived total variable name (e.g., "bw_swemwbs_total")
        available_columns: List of all available column names in the dataset

    Returns:
        List of constituent item column names

    Rules:
        - For a total like "bw_swemwbs_total", constituents are "bw_swemwbs_1", "bw_swemwbs_2", etc.
        - Prefix is everything before "_total"
        - Items end with "_<number>"
    """
    if not is_derived_total(variable):
        return []

    # Remove "_total" suffix to get the prefix
    prefix = variable[:-6]  # Remove "_total"
    prefix_with_underscore = prefix + "_"

    # Find all columns that match the pattern prefix_<number>
    constituents = []
    for col in available_columns:
        if col.startswith(prefix_with_underscore):
            # Check if it ends with _<number>
            suffix = col[len(prefix_with_underscore) :]
            if suffix.isdigit():
                constituents.append(col)

    return sorted(constituents)


def recompute_derived_total(
    df: pd.DataFrame, variable: str, constituent_items: list[str]
) -> pd.DataFrame:
    """Recompute a derived total from constituent item values in deduped data.

    Args:
        df: Deduped DataFrame
        variable: Derived total variable name
        constituent_items: List of constituent item column names

    Returns:
        DataFrame with the derived total recomputed

    Rules:
        - Sum across constituent items (skipna=True for row-wise sum)
        - Only recompute if all constituent items exist in the DataFrame
    """
    df = df.copy()

    # Check if all constituents exist
    missing = [item for item in constituent_items if item not in df.columns]
    if missing:
        logger.warning(
            f"Cannot recompute {variable}: missing constituent items {missing}"
        )
        return df

    # Recompute the total
    df[variable] = df[constituent_items].sum(axis=1, skipna=True)

    return df


def execute_query(
    df: pd.DataFrame,
    query: CanonicalQuery,
    numerical_whitelist: Optional[list[str]] = None,
    observed_periods: Optional[list[str]] = None,
    min_n: int = 5,
    form_metadata: Optional[dict] = None,
) -> dict:
    """Execute a period-oriented multi-variable query.

    Args:
        df: Normalized DataFrame with period_id column
        query: Canonical query with variables, dimensions, and prefixes
        numerical_whitelist: List of valid numerical variable names
        observed_periods: Pre-computed list of observed periods for this scope
        min_n: Minimum N for suppression
        form_metadata: Form metadata for version compatibility (min/max ranges)

    Returns:
        Query response dict matching NewQueryResponse model

    Rules:
        - Variables are selected by exact match (v) or prefix (variable_prefix)
        - If both are omitted, all numerical variables are selected
        - Results are organized by period within each variable
        - Periods with no data for a variable are omitted from that variable's results
        - Suppression is evaluated independently per period
    """
    # Determine which variables to query
    selected_variables = _select_variables(df, query, numerical_whitelist)

    # Use provided observed periods, or compute them if not provided
    if observed_periods is None:
        observed_periods = get_observed_periods(df)

    # Execute query for each variable
    variable_slices = []
    for variable in selected_variables:
        var_slice = _execute_variable_query(
            df=df,
            variable=variable,
            dimensions=query.dimensions,
            observed_periods=observed_periods,
            min_n=min_n,
            form_metadata=form_metadata,
        )
        variable_slices.append(var_slice)

    # Build response
    return {
        "query": query.model_dump(),
        "dimensions": query.dimensions,
        "periods": observed_periods,
        "variables": variable_slices,
    }


def _select_variables(
    df: pd.DataFrame,
    query: CanonicalQuery,
    numerical_whitelist: Optional[list[str]] = None,
) -> list[str]:
    """Select variables based on query parameters.

    Args:
        df: DataFrame with data
        query: Canonical query
        numerical_whitelist: List of valid numerical variables

    Returns:
        Sorted list of selected variable names

    Rules:
        - If both 'variables' and 'variable_prefixes' are empty, select all numerical variables
        - If 'variables' is specified, include those exact variables
        - If 'variable_prefixes' is specified, expand to matching variables
        - Union of both lists if both are specified
    """
    if numerical_whitelist is None:
        # Infer numerical columns from DataFrame
        numerical_whitelist = df.select_dtypes(include=["number"]).columns.tolist()
        # Remove non-variable columns
        numerical_whitelist = [
            col
            for col in numerical_whitelist
            if col not in ["uid", "wave", "period_id", "school_id"]
        ]

    # If no variables or prefixes specified, return all numerical variables
    if not query.variables and not query.variable_prefixes:
        return sorted(numerical_whitelist)

    # Start with empty set
    selected = set()

    # Add exact variable matches
    for var in query.variables:
        if var in numerical_whitelist:
            selected.add(var)

    # Add prefix-matched variables
    for prefix in query.variable_prefixes:
        for var in numerical_whitelist:
            if var.startswith(prefix) or get_variable_part(var).startswith(prefix):
                selected.add(var)

    return sorted(list(selected))


def _execute_variable_query(
    df: pd.DataFrame,
    variable: str,
    dimensions: list[str],
    observed_periods: list[str],
    min_n: int,
    form_metadata: Optional[dict] = None,
) -> dict:
    """Execute query for a single variable across all periods.

    Args:
        df: Normalized DataFrame
        variable: Variable name to query
        dimensions: List of dimension columns to group by
        observed_periods: List of observed period IDs
        min_n: Minimum N for suppression
        form_metadata: Optional metadata for version compatibility checking

    Returns:
        Variable slice dict with periods
    """
    # Deduplicate submissions for this variable first
    df_deduped = deduplicate_submissions(df, variable)

    # If this is a derived total, recompute it from deduped constituent items
    if is_derived_total(variable):
        constituent_items = get_constituent_items(variable, df.columns.tolist())
        if constituent_items:
            # First deduplicate each constituent item
            deduped_constituents = df_deduped.copy()
            for item in constituent_items:
                if item in df.columns:
                    # Deduplicate this constituent item
                    item_deduped = deduplicate_submissions(df, item)
                    # Merge the deduped values back
                    # Use uid, period_id, and school (if exists) as merge keys
                    merge_keys = ["uid", "period_id"]
                    if "school" in df.columns:
                        merge_keys.append("school")

                    # Keep only the item column from the deduped data
                    item_data = item_deduped[merge_keys + [item]]

                    # Drop the item column if it exists in deduped_constituents
                    if item in deduped_constituents.columns:
                        deduped_constituents = deduped_constituents.drop(columns=[item])

                    # Merge in the deduped item values
                    deduped_constituents = deduped_constituents.merge(
                        item_data, on=merge_keys, how="left"
                    )

            # Now recompute the total from deduped constituents
            df_deduped = recompute_derived_total(
                deduped_constituents, variable, constituent_items
            )

    # Build period results
    period_results = {}

    for period_id in observed_periods:
        # Filter to this period
        period_df = df_deduped[df_deduped["period_id"] == period_id]

        # Check if variable exists and has data in this period
        if variable not in period_df.columns:
            # Variable not collected in this period - omit from results
            continue

        # Drop rows where variable is null
        period_df = period_df[period_df[variable].notna()]

        if period_df.empty:
            # No data for this variable in this period - omit
            continue

        # Check version compatibility if we have version metadata
        notes = []
        question_versions = None
        version_col = get_version_column_name(variable)
        if form_metadata and version_col in period_df.columns:
            # Check if multiple versions exist in this period
            versions = period_df[version_col].dropna().unique()
            version_counts = period_df[version_col].value_counts()

            if len(versions) > 1:
                # Multiple versions - check compatibility
                from glow_api.version_compatibility import (
                    check_version_compatibility,
                    apply_rescaling,
                )

                # Determine reference version (use the most common one)
                reference_version = version_counts.index[0]
                _, base_variable = split_namespaced_variable(variable)
                ref_meta = get_version_metadata_map(
                    form_metadata, variable, str(reference_version)
                )

                # Check compatibility of all other versions against reference
                incompatible = False
                rescale_mapping = {}  # version -> (from_range, to_range)

                for version in versions:
                    if version == reference_version:
                        continue

                    ver_meta = get_version_metadata_map(
                        form_metadata, variable, str(version)
                    )

                    compat = check_version_compatibility(
                        base_variable, ver_meta, ref_meta
                    )

                    if not compat["compatible"]:
                        incompatible = True
                        break

                    if compat["rescale_needed"]:
                        rescale_mapping[version] = (
                            compat["rescale_from"],
                            compat["rescale_to"],
                        )

                if incompatible:
                    # Suppress this period due to incompatible versions
                    period_results[period_id] = {
                        "suppressed": True,
                        "suppression_reason": "incompatible-version",
                        "cells": None,
                    }
                    continue

                # Apply rescaling if needed
                if rescale_mapping:
                    period_df = period_df.copy()
                    for version, (from_range, to_range) in rescale_mapping.items():
                        # Rescale rows with this version
                        mask = period_df[version_col] == version
                        if mask.any():
                            period_df.loc[mask] = apply_rescaling(
                                period_df[mask],
                                variable,
                                from_range,
                                to_range,
                            )
                    notes.append("values-rescaled")

            # Record versions observed in this period
            question_versions = {
                str(version): int(count)
                for version, count in version_counts.items()
                if pd.notna(version)
            }

        # Compute aggregated results for this period
        period_slice = _compute_period_slice(
            period_df=period_df,
            variable=variable,
            dimensions=dimensions,
            min_n=min_n,
        )

        # Add notes if any
        if notes:
            period_slice["notes"] = notes

        # Add question_versions if we have them
        if question_versions:
            period_slice["question_versions"] = question_versions

        period_results[period_id] = period_slice

    return {
        "variable": variable,
        "periods": period_results,
    }


def _safe_mean(series: pd.Series) -> float | None:
    """Compute a mean, mapping NaN (e.g. all-missing group) to None for JSON compliance."""
    mean_value = series.mean()
    return None if pd.isna(mean_value) else float(mean_value)


def _compute_period_slice(
    period_df: pd.DataFrame,
    variable: str,
    dimensions: list[str],
    min_n: int,
) -> dict:
    """Compute aggregated results for a single variable in a single period.

    Args:
        period_df: DataFrame filtered to one period
        variable: Variable name
        dimensions: Dimensions to group by
        min_n: Minimum N for suppression

    Returns:
        Period slice dict with cells or suppression info
    """
    # If no dimensions, compute overall mean
    if not dimensions:
        n = len(period_df)
        if n < min_n:
            return {
                "suppressed": True,
                "suppression_reason": "small-n",
                "cells": None,
            }

        return {
            "suppressed": False,
            "cells": [{"mean": _safe_mean(period_df[variable]), "n": n}],
        }

    # Group by dimensions
    # Filter to only include valid dimensions that exist in the DataFrame
    valid_dimensions = [d for d in dimensions if d in period_df.columns]

    if not valid_dimensions:
        # No valid dimensions - fall back to overall mean
        n = len(period_df)
        if n < min_n:
            return {
                "suppressed": True,
                "suppression_reason": "small-n",
                "cells": None,
            }

        return {
            "suppressed": False,
            "cells": [{"mean": _safe_mean(period_df[variable]), "n": n}],
        }

    # Group and aggregate
    grouped = period_df.groupby(valid_dimensions, dropna=False)

    # Compute mean and count for each group
    cells = []
    for group_coords, group_df in grouped:
        n = len(group_df)

        # Build cell with coordinates
        cell = {"mean": _safe_mean(group_df[variable]), "n": n}

        # Add dimension coordinates. groupby(list-of-columns) always
        # returns tuple keys, even for a single dimension.
        for i, dim in enumerate(valid_dimensions):
            coord = group_coords[i]
            cell[dim] = None if pd.isna(coord) else coord

        cells.append(cell)

    # Check for blanket suppression
    min_cell_n = min(cell["n"] for cell in cells) if cells else 0
    if min_cell_n < min_n:
        return {
            "suppressed": True,
            "suppression_reason": "small-n",
            "cells": None,
        }

    return {
        "suppressed": False,
        "cells": cells,
    }
