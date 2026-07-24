import re
import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Final, Literal, NewType, Protocol, TypedDict, overload
from urllib.parse import SplitResult, urlsplit

from workflow import PasswordNotFound
from workflow.util import AcquisitionError

HOST_SCHEMA_VERSION: Final = 1
DEFAULT_API_URL: Final = "https://gitlab.com/api/v4/projects"
DOMAIN_LABEL: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

ProfileId = NewType("ProfileId", str)


class ProfileRecord(TypedDict):
    id: str
    name: str
    api_url: str


class SettingsUpdate(TypedDict):
    hosts: list[ProfileRecord]
    default_host_id: str
    host_schema_version: int


class SettingsStore(Protocol):
    @overload
    def get(
        self,
        key: Literal["hosts"],
        default: list[ProfileRecord],
    ) -> list[ProfileRecord]: ...

    @overload
    def get(
        self,
        key: Literal["default_host_id"],
        default: str,
    ) -> str: ...

    def update(self, values: SettingsUpdate) -> None: ...


class WorkflowLike(Protocol):
    settings: SettingsStore

    def save_password(self, account: str, password: str) -> None: ...

    def get_password(self, account: str) -> str: ...

    def delete_password(self, account: str) -> None: ...


RegistryCallback = Callable[[WorkflowLike, str], None]


def _ignore_profile(_workflow: WorkflowLike, _profile_id: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class RegistryCallbacks:
    invalidate: RegistryCallback = _ignore_profile
    cleanup: RegistryCallback = _ignore_profile


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

    @classmethod
    def from_record(cls, record: ProfileRecord) -> "HostProfile":
        return cls(
            id=ProfileId(record["id"]),
            name=record["name"],
            api_url=record["api_url"],
        )

    def to_record(self) -> ProfileRecord:
        return {
            "id": self.id,
            "name": self.name,
            "api_url": self.api_url,
        }


@dataclass(frozen=True, slots=True)
class HostRegistry:
    workflow: WorkflowLike
    callbacks: RegistryCallbacks = RegistryCallbacks()

    def profiles(self) -> tuple[HostProfile, ...]:
        return tuple(
            HostProfile.from_record(record)
            for record in get_profiles(self.workflow)
        )

    def add_or_update(self, draft: ProfileDraft) -> HostProfile:
        parsed = valid_api_url(draft.api_url)
        if parsed is None:
            raise ValueError(
                "GitLab API URL must be an absolute HTTP(S) URL"
            )
        name = derive_host_name(draft.api_url) if draft.name is None else draft.name
        if not name:
            raise ValueError("Host name cannot be empty")
        if any(character.isspace() for character in name):
            raise ValueError("Host name cannot contain whitespace")

        profiles = self.profiles()
        existing = next(
            (profile for profile in profiles if profile.name == name),
            None,
        )
        profile_id = existing.id if existing else ProfileId(new_profile_id())
        replacement = HostProfile(
            id=profile_id,
            name=name,
            api_url=draft.api_url,
        )
        updated = tuple(
            replacement if profile.id == profile_id else profile
            for profile in profiles
        )
        if existing is None:
            updated = (*updated, replacement)

        account = token_account(profile_id)
        previous_token = None
        if existing is not None:
            try:
                previous_token = self.workflow.get_password(account)
            except PasswordNotFound:
                previous_token = None
        self.workflow.save_password(account, draft.token)
        try:
            _save_profiles(
                self.workflow,
                updated,
                _selected_default_id(self.workflow, updated),
            )
        except (OSError, AcquisitionError):
            if previous_token is None:
                self.workflow.delete_password(account)
            else:
                self.workflow.save_password(account, previous_token)
            raise
        self.callbacks.invalidate(self.workflow, profile_id)
        return replacement

    def remove(self, name: str) -> HostProfile:
        profiles = self.profiles()
        removed = next(
            (profile for profile in profiles if profile.name == name),
            None,
        )
        if removed is None:
            raise ValueError(f"Unknown GitLab host profile: {name}")
        remaining = tuple(
            profile for profile in profiles if profile.id != removed.id
        )
        self.workflow.delete_password(token_account(removed.id))
        self.callbacks.cleanup(self.workflow, removed.id)
        _save_profiles(
            self.workflow,
            remaining,
            _selected_default_id(self.workflow, remaining),
        )
        return removed


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
        raise ValueError("GitLab API URL must be an absolute HTTP(S) URL")

    hostname = parsed.hostname
    assert hostname is not None
    try:
        ip = ip_address(hostname)
    except ValueError:
        authority = hostname
    else:
        authority = f"[{hostname}]" if ip.version == 6 else hostname
    return f"{authority}:{parsed.port}" if parsed.port else authority


def parse_host_add(argument: str) -> tuple[str | None, str, str]:
    parts = shlex.split(argument)
    if len(parts) == 2:
        return None, parts[0], parts[1]
    if len(parts) == 3:
        if any(char.isspace() for char in parts[0]):
            raise ValueError("Host name cannot contain whitespace")
        return parts[0], parts[1], parts[2]
    raise ValueError("Usage: glhostadd [name] <api_url> <token>")


def token_account(profile_id: str) -> str:
    return f"gitlab_api_key:{profile_id}"


def new_profile_id() -> str:
    return uuid.uuid4().hex


def get_profiles(workflow: WorkflowLike) -> list[ProfileRecord]:
    profiles = workflow.settings.get("hosts", [])
    return [
        ProfileRecord(
            id=profile["id"],
            name=profile["name"],
            api_url=profile["api_url"],
        )
        for profile in profiles
    ]


def _save_profiles(
    workflow: WorkflowLike,
    profiles: tuple[HostProfile, ...],
    default_host_id: ProfileId | None,
) -> None:
    workflow.settings.update(
        {
            "hosts": [profile.to_record() for profile in profiles],
            "default_host_id": default_host_id or "",
            "host_schema_version": HOST_SCHEMA_VERSION,
        }
    )


def _selected_default_id(
    workflow: WorkflowLike,
    profiles: tuple[HostProfile, ...],
) -> ProfileId | None:
    configured_id = workflow.settings.get("default_host_id", "")
    configured = next(
        (profile.id for profile in profiles if profile.id == configured_id),
        None,
    )
    if configured is not None:
        return configured
    return profiles[0].id if profiles else None


def get_default_profile(workflow: WorkflowLike) -> ProfileRecord | None:
    profiles = tuple(
        HostProfile.from_record(record) for record in get_profiles(workflow)
    )
    profile_id = _selected_default_id(workflow, profiles)
    selected = next(
        (profile for profile in profiles if profile.id == profile_id),
        None,
    )
    return selected.to_record() if selected else None


def add_or_update_profile(
    registry: HostRegistry,
    draft: ProfileDraft,
) -> ProfileRecord:
    return registry.add_or_update(draft).to_record()


def remove_profile(registry: HostRegistry, name: str) -> ProfileRecord:
    return registry.remove(name).to_record()
