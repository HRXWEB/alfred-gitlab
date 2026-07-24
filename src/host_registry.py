from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypedDict, overload

from host_values import (
    HostProfile,
    InvalidApiUrlError,
    InvalidHostNameError,
    ProfileDraft,
    ProfileId,
    ProfileRecord,
    derive_host_name,
    valid_api_url,
)
from workflow import PasswordNotFound
from workflow.util import AcquisitionError

HOST_SCHEMA_VERSION: Final = 1
DEFAULT_API_URL: Final = "https://gitlab.com/api/v4/projects"
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

    @overload
    def get(
        self,
        key: Literal["host_schema_version"],
        default: int,
    ) -> int: ...

    @overload
    def get(
        self,
        key: Literal["api_url"],
        default: str,
    ) -> str: ...

    def update(self, values: SettingsUpdate) -> None: ...

    def __contains__(self, key: str) -> bool: ...

    def __setitem__(
        self,
        key: str,
        value: list[ProfileRecord] | str | int,
    ) -> None: ...

    def __delitem__(self, key: str) -> None: ...


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
class UnknownHostProfileError(ValueError):
    name: str

    def __str__(self) -> str:
        return f"Unknown GitLab host profile: {self.name}"


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
            raise InvalidApiUrlError
        name = derive_host_name(draft.api_url) if draft.name is None else draft.name
        if not name:
            raise InvalidHostNameError("name cannot be empty")
        if any(character.isspace() for character in name):
            raise InvalidHostNameError("whitespace is not allowed")

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
            raise UnknownHostProfileError(name)
        remaining = tuple(
            profile for profile in profiles if profile.id != removed.id
        )
        account = token_account(removed.id)
        token = self.workflow.get_password(account)
        self.callbacks.cleanup(self.workflow, removed.id)
        _save_profiles(
            self.workflow,
            remaining,
            _selected_default_id(self.workflow, remaining),
        )
        try:
            self.workflow.delete_password(account)
        except (OSError, AcquisitionError):
            self.workflow.save_password(account, token)
            _save_profiles(
                self.workflow,
                profiles,
                _selected_default_id(self.workflow, profiles),
            )
            raise
        return removed


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
