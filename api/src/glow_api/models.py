from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, RootModel


class SuppressionCode(str, Enum):
    SMALL_N = "<5"


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    school_ids: list[int] = Field(default_factory=list)
    is_admin: bool = False


class UserRead(BaseModel):
    id: int
    username: str
    school_ids: list[int] = Field(default_factory=list)
    school_names: list[str] = Field(default_factory=list)
    is_active: bool
    is_admin: bool = False
    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    password: Optional[str] = None
    school_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class SchoolCreate(BaseModel):
    name: str
    size: Optional[str] = None
    category: Optional[str] = None
    odk_school_id: Optional[str] = None


class SchoolRead(BaseModel):
    id: int
    name: str
    size: Optional[str] = None
    category: Optional[str] = None
    odk_school_id: Optional[str] = None
    geographical_neighbor_ids: list[int] = Field(default_factory=list)
    statistical_neighbor_ids: list[int] = Field(default_factory=list)
    model_config = {"from_attributes": True}


class SchoolListResponse(RootModel[list[SchoolRead]]):
    """List of schools response wrapper for OpenAPI examples."""

    pass


class SchoolUpdate(BaseModel):
    name: Optional[str] = None
    size: Optional[str] = None
    category: Optional[str] = None
    odk_school_id: Optional[str] = None
    geographical_neighbor_ids: Optional[list[int]] = None
    statistical_neighbor_ids: Optional[list[int]] = None


# ---------------------------------------------------------------------------
# /me endpoint models
# ---------------------------------------------------------------------------


class SchoolSummary(BaseModel):
    """Summary of a school for the /me endpoint."""

    id: int
    name: str


class MeAnonymous(BaseModel):
    """Response for /me when no valid token is provided."""

    kind: Literal["anonymous"] = "anonymous"


class MeAuthenticated(BaseModel):
    """Response for /me when a valid token is provided."""

    kind: Literal["authenticated"] = "authenticated"
    id: int
    username: str
    is_admin: bool
    schools: list[SchoolSummary]


MeResponse = Union[MeAnonymous, MeAuthenticated]


# ---------------------------------------------------------------------------
# /dimensions endpoint models
# ---------------------------------------------------------------------------


class DimensionDefinition(BaseModel):
    """Definition of a single dimension."""

    key: str
    type: Literal["string", "number"]


class VariableDefinition(BaseModel):
    """Definition of a single variable."""

    key: str
    raw_key: Optional[str] = None
    form_id: Optional[str] = None


class DimensionsResponse(BaseModel):
    """Response for /dimensions endpoint."""

    school_id: Optional[int] = None
    variables: list[VariableDefinition]
    dimensions: list[DimensionDefinition]


# ---------------------------------------------------------------------------
# New Period-Based Query Models
# ---------------------------------------------------------------------------


class CanonicalQuery(BaseModel):
    """Normalized internal form of a query request.

    Used for response echoes, deterministic behavior, and ETag generation.
    All lists are sorted and deduped.
    """

    school_id: Optional[int] = None
    variables: list[str] = Field(default_factory=list)  # Sorted, deduped
    dimensions: list[str] = Field(default_factory=list)  # Sorted, deduped
    variable_prefixes: list[str] = Field(default_factory=list)  # Sorted, deduped


class PeriodSliceCell(BaseModel):
    """A single cell in a period slice result."""

    mean: Optional[float] = None
    n: int
    # Dynamic coordinate fields based on requested dimensions
    model_config = {"extra": "allow"}


class PeriodSlice(BaseModel):
    """Results or suppression metadata for a single variable within a period."""

    suppressed: bool = False
    suppression_reason: Optional[Literal["small-n", "incompatible-version"]] = None
    notes: list[Literal["values-rescaled"]] = Field(default_factory=list)
    question_versions: Optional[dict[str, int]] = None  # version -> count
    cells: Optional[list[PeriodSliceCell]] = None


class VariableSlice(BaseModel):
    """Results for a single variable across periods."""

    variable: str
    # period_id -> PeriodSlice (missing keys mean not collected/not applicable)
    periods: dict[str, PeriodSlice]


class NewQueryResponse(BaseModel):
    """New period-oriented multi-variable query response.

    Replaces the old wave-first, neighbor-oriented QueryResponse.
    """

    query: CanonicalQuery  # Echo of the normalized request
    dimensions: list[str]  # Ordered list of requested dimension keys
    periods: list[str]  # Observed period IDs in chronological order
    variables: list[VariableSlice]  # One per requested variable


# ---------------------------------------------------------------------------
# Error Response Models
# ---------------------------------------------------------------------------


class ErrorDetailResponse(BaseModel):
    """Standard error response format for API errors."""

    detail: str
