import re
import shlex
from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
from typing import Final, NewType, TypedDict
from urllib.parse import SplitResult, urlsplit

DOMAIN_LABEL: Final = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)

ProfileId = NewType("ProfileId", str)


class NameSource(str, Enum):
    AUTO = "auto"
    CUSTOM = "custom"


class ProfileRecord(TypedDict):
    id: str
    name: str
    api_url: str
    name_source: NameSource


@dataclass(frozen=True, slots=True)
class InvalidApiUrlError(ValueError):
    def __str__(self) -> str:
        return "GitLab API URL must be an absolute HTTP(S) URL"


@dataclass(frozen=True, slots=True)
class InvalidHostNameError(ValueError):
    reason: str

    def __str__(self) -> str:
        return f"Invalid GitLab host name: {self.reason}"


@dataclass(frozen=True, slots=True)
class InvalidHostCommandError(ValueError):
    def __str__(self) -> str:
        return "Usage: glhostadd [name] <api_url> <token>"


@dataclass(frozen=True, slots=True)
class ProfileDraft:
    name: str | None
    api_url: str
    token: str


@dataclass(frozen=True, slots=True)
class HostProfile:
    id: ProfileId
    name: str
    api_url: str
    name_source: NameSource

    @classmethod
    def from_record(cls, record: ProfileRecord) -> "HostProfile":
        return cls(
            id=ProfileId(record["id"]),
            name=record["name"],
            api_url=record["api_url"],
            name_source=NameSource(record["name_source"]),
        )

    def to_record(self) -> ProfileRecord:
        return {
            "id": self.id,
            "name": self.name,
            "api_url": self.api_url,
            "name_source": self.name_source,
        }


def valid_api_url(api_url: str) -> SplitResult | None:
    try:
        parsed = urlsplit(api_url)
        hostname, _ = parsed.hostname, parsed.port
    except (TypeError, ValueError):
        return None

    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or parsed.username
        or parsed.password
    ):
        return None

    try:
        _ = ip_address(hostname)
    except ValueError:
        domain = hostname.rstrip(".")
        if (
            len(domain) > 253
            or not all(DOMAIN_LABEL.match(label) for label in domain.split("."))
        ):
            return None

    return parsed


def derive_host_name(api_url: str) -> str:
    parsed = valid_api_url(api_url)
    if parsed is None:
        raise InvalidApiUrlError

    hostname = parsed.hostname
    assert hostname is not None
    try:
        ip = ip_address(hostname)
    except ValueError:
        authority = hostname
    else:
        authority = f"[{hostname}]" if ip.version == 6 else hostname
    return f"{authority}:{parsed.port}" if parsed.port else authority


def parse_host_add(argument: str) -> ProfileDraft:
    parts = shlex.split(argument)
    if len(parts) == 2:
        return ProfileDraft(name=None, api_url=parts[0], token=parts[1])
    if len(parts) == 3:
        if any(char.isspace() for char in parts[0]):
            raise InvalidHostNameError("whitespace is not allowed")
        return ProfileDraft(name=parts[0], api_url=parts[1], token=parts[2])
    raise InvalidHostCommandError
