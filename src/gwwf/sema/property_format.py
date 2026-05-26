import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BeforeValidator


# --- patterns ---
LEFT_RIGHT_DOT_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(\.[a-z0-9]+)*$"
)


# --- methods ---
def is_left_right_dot(v: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"<{v}>: LeftRightDot must be a string.")

    if not LEFT_RIGHT_DOT_PATTERN.fullmatch(v):
        raise ValueError(f"<{v}>: Fails LeftRightDot format.")

    return v


def is_utc_seconds(v: int) -> int:
    if not isinstance(v, int):
        raise ValueError("Not an int!")
    start_date = datetime(2000, 1, 1, tzinfo=UTC)
    end_date = datetime(3000, 1, 1, tzinfo=UTC)

    start_timestamp = int(start_date.timestamp())
    end_timestamp = int(end_date.timestamp())

    if v < start_timestamp:
        raise ValueError(f"{v}: Fails UTCSeconds format! Must be after Jan 1 2000")
    if v > end_timestamp:
        raise ValueError(f"{v}: Fails UTCSeconds format! Must be before Jan 1 3000")
    return v


# --- annotated types ---
LeftRightDot = Annotated[
    str,
    BeforeValidator(is_left_right_dot),
]

UTCSeconds = Annotated[
    int,
    BeforeValidator(is_utc_seconds),
]
