# -*- coding: utf-8 -*-
from agentscope_runtime.engine.schemas.exception import (
    ModelContextLengthExceededException,
    ModelExecutionException,
)
import pytest

from qwenpaw.exceptions import convert_model_exception


class FakeAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("message", "status_code", "expected_type"),
    [
        (
            "maximum context length exceeded",
            400,
            ModelContextLengthExceededException,
        ),
        (
            "Prompt is too long for this model",
            400,
            ModelContextLengthExceededException,
        ),
        ("request too large", 413, ModelExecutionException),
    ],
)
def test_context_length_exception_mapping(
    message: str,
    status_code: int,
    expected_type: type,
) -> None:
    error = FakeAPIError(
        message,
        status_code=status_code,
    )

    converted = convert_model_exception(error, "test-model")

    assert isinstance(converted, expected_type)
