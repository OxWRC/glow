"""Test that all contract examples validate against their declared Pydantic models."""

import pytest
from pydantic import TypeAdapter, ValidationError

from glow_api.contract_examples import iter_contract_examples
from glow_api import models


# Map of model names to actual model classes
MODEL_MAP = {
    "TokenResponse": models.Token,
    "UserRead": models.UserRead,
    "SchoolListResponse": models.SchoolListResponse,
    "ErrorDetailResponse": models.ErrorDetailResponse,
    "MeAnonymous": models.MeAnonymous,
    "MeAuthenticated": models.MeAuthenticated,
    "DimensionsResponse": models.DimensionsResponse,
    "NewQueryResponse": models.NewQueryResponse,
    "DemoInfo": models.DemoInfo,
}


@pytest.mark.parametrize("example_id,example", list(iter_contract_examples()))
def test_contract_example_validates(example_id: str, example: dict):
    """Validate that each contract example conforms to its declared model."""
    response_model_name = example.get("response_model")
    request_model_name = example.get("request_model")

    # Validate response if model is specified
    if response_model_name:
        model_class = MODEL_MAP.get(response_model_name)
        if not model_class:
            pytest.fail(
                f"Unknown response model '{response_model_name}' in example '{example_id}'"
            )

        response_data = example.get("response")
        try:
            adapter = TypeAdapter(model_class)
            adapter.validate_python(response_data)
        except ValidationError as e:
            pytest.fail(
                f"Response validation failed for example '{example_id}' "
                f"(model: {response_model_name}):\n{e}"
            )

    # Validate request if model is specified
    if request_model_name:
        model_class = MODEL_MAP.get(request_model_name)
        if not model_class:
            pytest.fail(
                f"Unknown request model '{request_model_name}' in example '{example_id}'"
            )

        request_data = example.get("request")
        if request_data is not None:  # Allow null requests for GET endpoints
            try:
                adapter = TypeAdapter(model_class)
                adapter.validate_python(request_data)
            except ValidationError as e:
                pytest.fail(
                    f"Request validation failed for example '{example_id}' "
                    f"(model: {request_model_name}):\n{e}"
                )
