"""Shared structural rules; filesystem checks belong to the future run service."""

from decimal import Decimal, InvalidOperation
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints,
    ValidationInfo,
)

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Period = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-Q[1-4]$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"


def unique(values: list, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate values")


def scoped_path(value: str, root: str) -> str:
    parts = value.split("/")
    if (len(parts) < 2 or parts[0] != root
            or any(part in {"", ".", ".."} for part in parts)
            or any(char in value for char in ("\\", ":", "?", "#"))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"path must be a canonical relative path under {root}/")
    return value


InputPath = Annotated[str, AfterValidator(lambda value: scoped_path(value, "inputs"))]
ArtifactPath = Annotated[str, AfterValidator(lambda value: scoped_path(value, "artifacts"))]


def exact_decimal(value: object, info: ValidationInfo) -> Decimal:
    # Decimal strings are the canonical JSON representation. Floats and bools
    # are rejected, so binary rounding and True == 1 cannot enter the contract.
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("use an exact decimal string or integer, not float/bool")
    if type(value) is int:
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    if info.mode == "json" and isinstance(value, str):
        if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value):
            raise ValueError("invalid decimal string")
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValueError("invalid decimal string") from None
    raise ValueError("use Decimal in Python or a decimal string in JSON")


ExactDecimal = Annotated[Decimal, BeforeValidator(exact_decimal, json_schema_input_type=int | str)]
