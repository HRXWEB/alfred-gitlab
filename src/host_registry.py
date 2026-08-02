from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from host_registry_state import (
    WorkflowLike,
    restore_registry_settings,
    snapshot_registry_settings,
)
from host_values import (
    HostProfile,
    InvalidApiUrlError,
    InvalidHostNameError,
    NameSource,
    ProfileDraft,
    ProfileId,
    ProfileRecord,
    derive_host_name,
    valid_api_url,
)
from workflow import KeychainError, PasswordNotFound
from workflow.util import AcquisitionError

HOST_SCHEMA_VERSION: Final = 1
DEFAULT_API_URL: Final = "https://gitlab.com/api/v4/projects"


RegistryCallback = Callable[[WorkflowLike, str], None]


def _ignore_profile(_workflow: WorkflowLike, _profile_id: str) -> None:
    return None


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class RegistryCallbacks:
    invalidate: RegistryCallback = _ignore_profile
    cleanup: RegistryCallback = _ignore_profile
    restore: RegistryCallback = _ignore_profile


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class UnknownHostProfileError(ValueError):
    name: str

    def __str__(self) -> str:
        return f"Unknown GitLab host profile: {self.name}"


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class UnknownHostProfileIdError(ValueError):
    profile_id: str

    def __str__(self) -> str:
        return "Unknown GitLab host profile ID"


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class HostRegistry:
    workflow: WorkflowLike
    callbacks: RegistryCallbacks = RegistryCallbacks()

    def profiles(self) -> tuple[HostProfile, ...]:
        return tuple(
            HostProfile.from_record(record)
            for record in get_profiles(self.workflow)
        )

    def set_default(self, profile_id: str) -> HostProfile:
        profiles = self.profiles()
        selected = next(
            (profile for profile in profiles if profile.id == profile_id), None
        )
        if selected is None:
            raise UnknownHostProfileIdError(profile_id)
        snapshot = snapshot_registry_settings(
            self.workflow, [profile.to_record() for profile in profiles]
        )
        try:
            _save_profiles(self.workflow, profiles, selected.id)
        except (OSError, AcquisitionError, KeychainError):
            _ = restore_registry_settings(self.workflow, snapshot)
            raise
        return selected

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
        records = [profile.to_record() for profile in profiles]
        settings_state = snapshot_registry_settings(self.workflow, records)
        existing = next(
            (profile for profile in profiles if profile.name == name),
            None,
        )
        profile_id = existing.id if existing else ProfileId(new_profile_id())
        replacement = HostProfile(
            id=profile_id,
            name=name,
            api_url=draft.api_url,
            name_source=(
                NameSource.AUTO
                if draft.name is None
                else NameSource.CUSTOM
            ),
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
            except PasswordNotFound as error:
                _ = error
                previous_token = None
        try:
            self.workflow.save_password(account, draft.token)
            _save_profiles(
                self.workflow,
                updated,
                _selected_default_id(self.workflow, updated),
            )
        except (OSError, AcquisitionError, KeychainError):
            _restore_token(self.workflow, account, previous_token)
            _ = restore_registry_settings(self.workflow, settings_state)
            raise
        self.callbacks.invalidate(self.workflow, profile_id)
        return replacement

    def remove(self, name: str) -> HostProfile:
        profiles = self.profiles()
        records = [profile.to_record() for profile in profiles]
        settings_state = snapshot_registry_settings(self.workflow, records)
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
        try:
            token = self.workflow.get_password(account)
        except PasswordNotFound:
            token = None
        self.callbacks.cleanup(self.workflow, removed.id)
        try:
            _save_profiles(
                self.workflow,
                remaining,
                _selected_default_id(self.workflow, remaining),
            )
            if token is not None:
                self.workflow.delete_password(account)
        except (OSError, AcquisitionError, KeychainError):
            try:
                if token is not None:
                    self.workflow.save_password(account, token)
            except KeychainError as error:
                _ = error
            _ = restore_registry_settings(self.workflow, settings_state)
            try:
                self.callbacks.restore(self.workflow, removed.id)
            except (OSError, AcquisitionError) as error:
                _ = error
            raise
        return removed


def _restore_token(
    workflow: WorkflowLike,
    account: str,
    previous_token: str | None,
) -> None:
    try:
        if previous_token is None:
            workflow.delete_password(account)
        else:
            workflow.save_password(account, previous_token)
    except (PasswordNotFound, KeychainError) as error:
        _ = error


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
            name_source=profile["name_source"],
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


def set_default_profile(
    registry: HostRegistry,
    profile_id: str,
) -> ProfileRecord:
    return registry.set_default(profile_id).to_record()


def remove_profile(registry: HostRegistry, name: str) -> ProfileRecord:
    return registry.remove(name).to_record()
