from dataclasses import dataclass
from typing import Protocol

from cache_state import Projects
from host_registry import (
    DEFAULT_API_URL,
    HOST_SCHEMA_VERSION,
    HostRegistry,
    RegistryCallbacks,
    WorkflowLike,
    _save_profiles,
    _selected_default_id,
    get_default_profile,
    get_profiles,
    new_profile_id,
    token_account,
)
from host_values import (
    HostProfile,
    InvalidApiUrlError,
    ProfileDraft,
    ProfileId,
    ProfileRecord,
    derive_host_name,
    valid_api_url,
)
from workflow import PasswordNotFound
from workflow.util import AcquisitionError


class HostCache(Protocol):
    def load_legacy_projects(self) -> Projects | None: ...

    def migrate_legacy_projects(
        self,
        profile_id: str,
        projects: Projects,
    ) -> bool: ...

    def invalidate_projects(self, profile_id: str) -> None: ...

    def clear_profile_state(self, profile_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CacheMigrationConflictError(OSError):
    profile_id: str


@dataclass(frozen=True, slots=True)
class RegistrySettingsSnapshot:
    had_hosts: bool
    hosts: list[ProfileRecord]
    had_default: bool
    default_host_id: str
    had_schema: bool
    schema_version: int


def ensure_profiles(
    workflow: WorkflowLike,
    cache: HostCache,
) -> list[ProfileRecord]:
    profiles = get_profiles(workflow)
    if (
        workflow.settings.get("host_schema_version", 0)
        == HOST_SCHEMA_VERSION
    ):
        return profiles
    if profiles:
        typed_profiles = tuple(
            HostProfile.from_record(profile) for profile in profiles
        )
        _save_profiles(
            workflow,
            typed_profiles,
            _selected_default_id(workflow, typed_profiles),
        )
        return profiles
    migrated = migrate_legacy_profile(workflow, cache)
    return [migrated] if migrated is not None else []


def migrate_legacy_profile(
    workflow: WorkflowLike,
    cache: HostCache,
) -> ProfileRecord | None:
    legacy_url = workflow.settings.get("api_url", "")
    try:
        legacy_token = workflow.get_password("gitlab_api_key")
    except PasswordNotFound:
        legacy_token = None
    legacy_projects = cache.load_legacy_projects()
    if not legacy_url and legacy_token is None and legacy_projects is None:
        return None

    api_url = legacy_url or DEFAULT_API_URL
    if valid_api_url(api_url) is None:
        raise InvalidApiUrlError
    profile = HostProfile(
        id=ProfileId(new_profile_id()),
        name=derive_host_name(api_url),
        api_url=api_url,
    )
    settings_snapshot = _snapshot_registry_settings(workflow)
    account = token_account(profile.id)
    token_attempted = False
    cache_written = False
    try:
        if legacy_token is not None:
            token_attempted = True
            workflow.save_password(account, legacy_token)
        if legacy_projects is not None:
            if cache.migrate_legacy_projects(profile.id, legacy_projects) is False:
                raise CacheMigrationConflictError(profile.id)
            cache_written = True
        _save_profiles(workflow, (profile,), profile.id)
    except (OSError, AcquisitionError):
        _restore_registry_settings(workflow, settings_snapshot)
        if cache_written:
            cache.clear_profile_state(profile.id)
        if token_attempted:
            _delete_new_token(workflow, account)
        raise
    return profile.to_record()


def set_default_token(
    workflow: WorkflowLike,
    token: str,
    cache: HostCache,
) -> ProfileRecord:
    _ = ensure_profiles(workflow, cache)
    selected = get_default_profile(workflow)
    if selected is None:
        registry = HostRegistry(
            workflow,
            RegistryCallbacks(
                invalidate=lambda _workflow, profile_id: (
                    cache.invalidate_projects(profile_id)
                )
            ),
        )
        return registry.add_or_update(
            ProfileDraft(
                name=None,
                api_url=DEFAULT_API_URL,
                token=token,
            )
        ).to_record()
    workflow.save_password(token_account(selected["id"]), token)
    cache.invalidate_projects(selected["id"])
    return selected


def set_default_url(
    workflow: WorkflowLike,
    api_url: str,
    cache: HostCache,
) -> ProfileRecord:
    if valid_api_url(api_url) is None:
        raise InvalidApiUrlError
    profiles = ensure_profiles(workflow, cache)
    selected = get_default_profile(workflow)
    if selected is None:
        profile = HostProfile(
            id=ProfileId(new_profile_id()),
            name=derive_host_name(api_url),
            api_url=api_url,
        )
        _save_profiles(workflow, (profile,), profile.id)
    else:
        profile = _updated_default_profile(selected, api_url)
        updated = tuple(
            profile
            if stored["id"] == selected["id"]
            else HostProfile.from_record(stored)
            for stored in profiles
        )
        _save_profiles(
            workflow,
            updated,
            _selected_default_id(workflow, updated),
        )
    cache.invalidate_projects(profile.id)
    return profile.to_record()


def _updated_default_profile(
    selected: ProfileRecord,
    api_url: str,
) -> HostProfile:
    name = selected["name"]
    if name == derive_host_name(selected["api_url"]):
        name = derive_host_name(api_url)
    return HostProfile(
        id=ProfileId(selected["id"]),
        name=name,
        api_url=api_url,
    )


def _snapshot_registry_settings(
    workflow: WorkflowLike,
) -> RegistrySettingsSnapshot:
    return RegistrySettingsSnapshot(
        had_hosts="hosts" in workflow.settings,
        hosts=get_profiles(workflow),
        had_default="default_host_id" in workflow.settings,
        default_host_id=workflow.settings.get("default_host_id", ""),
        had_schema="host_schema_version" in workflow.settings,
        schema_version=workflow.settings.get("host_schema_version", 0),
    )


def _restore_registry_settings(
    workflow: WorkflowLike,
    snapshot: RegistrySettingsSnapshot,
) -> None:
    _restore_setting(
        workflow,
        "hosts",
        snapshot.had_hosts,
        snapshot.hosts,
    )
    _restore_setting(
        workflow,
        "default_host_id",
        snapshot.had_default,
        snapshot.default_host_id,
    )
    _restore_setting(
        workflow,
        "host_schema_version",
        snapshot.had_schema,
        snapshot.schema_version,
    )


def _restore_setting(
    workflow: WorkflowLike,
    key: str,
    existed: bool,
    value: list[ProfileRecord] | str | int,
) -> None:
    if existed:
        workflow.settings[key] = value
    elif key in workflow.settings:
        del workflow.settings[key]


def _delete_new_token(workflow: WorkflowLike, account: str) -> None:
    try:
        workflow.delete_password(account)
    except PasswordNotFound:
        return
