"""Router for the /dimensions endpoint."""

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional

from glow_api import request_context
from glow_api.auth import get_optional_school_user
from glow_api.data import DataStore, get_datastore
from glow_api.database import get_db
from glow_api.models import DimensionsResponse, VariableDefinition, DimensionDefinition

router = APIRouter(tags=["discovery"])

# Use HTTPBearer with auto_error=False to make auth optional for dataset-scoped queries
security = HTTPBearer(auto_error=False)


def split_variable_key(variable_key: str) -> tuple[Optional[str], str]:
    """Split a namespaced variable key into form id and raw field name."""
    if "__" in variable_key:
        form_id, raw_key = variable_key.split("__", 1)
        return form_id, raw_key
    return None, variable_key


@router.get("/dimensions", response_model=DimensionsResponse)
def get_dimensions(
    school_id: Optional[int] = Query(
        None, description="Optional school ID for school-scoped discovery"
    ),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
    datastore: DataStore = Depends(get_datastore),
) -> DimensionsResponse:
    """Get available variables and dimensions.

    This endpoint supports both dataset-scoped and school-scoped discovery:
    - No school_id: returns public dataset-scoped dimensions (anonymous access OK)
    - With school_id: returns school-scoped dimensions (requires authorization)

    Returns:
        DimensionsResponse with variables and dimensions available for querying
    """
    # If school_id is provided, we need to check authorization
    if school_id is not None:
        _user, school = get_optional_school_user(credentials, db, school_id)

        # Get data scoped to this school
        dfwl = datastore.to_frozen()
        df = dfwl.df
        if "school" in df.columns and school.odk_school_id is not None:
            df = df[df["school"] == school.odk_school_id]
        else:
            df = df.iloc[0:0]  # no data loaded yet, or school not linked to ODK data
    else:
        request_context.record_event("auth_assessed", outcome="anonymous", success=None, school_id=None)
        # Dataset-scoped query - use full dataset
        dfwl = datastore.to_frozen()
        df = dfwl.df

    # Build variables list (all numeric measures including derived totals)
    variables = sorted([col for col in dfwl.numerical_whitelist if col in df.columns])
    variable_defs = []
    for var in variables:
        form_id, raw_key = split_variable_key(var)
        variable_defs.append(
            VariableDefinition(
                key=var,
                raw_key=raw_key,
                form_id=form_id,
            )
        )

    # Build dimensions list (categorical columns, excluding school)
    # Infer type based on whether column is numeric or not
    dimension_defs = []
    for dim in sorted(dfwl.categorical_whitelist):
        if dim in df.columns and dim not in ["school", "wave"]:
            # Determine dimension type based on pandas dtype
            col_dtype = df[dim].dtype
            if pd.api.types.is_numeric_dtype(col_dtype):
                dim_type = "number"
            else:
                dim_type = "string"

            dimension_defs.append(DimensionDefinition(key=dim, type=dim_type))

    request_context.record_event(
        "dispatch", variables=len(variable_defs), dimensions=len(dimension_defs)
    )
    return DimensionsResponse(
        school_id=school_id,
        variables=variable_defs,
        dimensions=dimension_defs,
    )
