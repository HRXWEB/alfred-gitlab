import pickle
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypedDict

PROFILE_ID: Final = re.compile(r"^[0-9a-f]{32}$")
JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
Project = dict[str, JSONValue]
Projects = list[Project]
CacheValue = JSONValue
CachePayload = CacheValue
CACHE_ERRORS: Final = (
    pickle.UnpicklingError,
    EOFError,
    AttributeError,
    ImportError,
    IndexError,
    OverflowError,
    UnicodeDecodeError,
    ValueError,
)


class StatusRecord(TypedDict, total=False):
    ok: bool
    category: str
    http_status: int | None
    message: str
    updated_at: int


@dataclass(frozen=True, slots=True)
class InvalidProfileIdError(ValueError):
    profile_id: str

    def __str__(self) -> str:
        return "Invalid GitLab host profile ID"


@dataclass(frozen=True, slots=True)
class InvalidStatusError(ValueError):
    field: str

    def __str__(self) -> str:
        return f"Invalid GitLab cache status field: {self.field}"


def validate_profile_id(profile_id: str) -> str:
    if PROFILE_ID.fullmatch(profile_id) is None:
        raise InvalidProfileIdError(profile_id)
    return profile_id


def projects_key(profile_id: str) -> str:
    return f"projects-{validate_profile_id(profile_id)}"


def status_key(profile_id: str) -> str:
    return f"status-{validate_profile_id(profile_id)}"


def sanitized_status(
    status: Mapping[str, JSONValue],
) -> StatusRecord:
    sanitized: StatusRecord = {}
    if "ok" in status:
        ok = status["ok"]
        if not isinstance(ok, bool):
            raise InvalidStatusError("ok")
        sanitized["ok"] = ok
    if "category" in status:
        category = status["category"]
        if not isinstance(category, str):
            raise InvalidStatusError("category")
        sanitized["category"] = category
    if "http_status" in status:
        http_status = status["http_status"]
        if http_status is not None and (
            not isinstance(http_status, int) or isinstance(http_status, bool)
        ):
            raise InvalidStatusError("http_status")
        sanitized["http_status"] = http_status
    if "message" in status:
        message = status["message"]
        if not isinstance(message, str):
            raise InvalidStatusError("message")
        sanitized["message"] = message
    if "updated_at" in status:
        updated_at = status["updated_at"]
        if not isinstance(updated_at, int) or isinstance(updated_at, bool):
            raise InvalidStatusError("updated_at")
        sanitized["updated_at"] = updated_at
    return sanitized


def status_payload(status: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    sanitized = sanitized_status(status)
    payload: dict[str, JSONValue] = {}
    if "ok" in sanitized:
        payload["ok"] = sanitized["ok"]
    if "category" in sanitized:
        payload["category"] = sanitized["category"]
    if "http_status" in sanitized:
        payload["http_status"] = sanitized["http_status"]
    if "message" in sanitized:
        payload["message"] = sanitized["message"]
    if "updated_at" in sanitized:
        payload["updated_at"] = sanitized["updated_at"]
    return payload
