from dataclasses import dataclass
from typing import Protocol, cast

from host_registry import WorkflowLike
from host_values import ProfileRecord
from workflow import PasswordNotFound
from workflow.util import AcquisitionError

ROLLBACK_ERRORS = (OSError, AcquisitionError, PasswordNotFound)


class MigrationCache(Protocol):
    def clear_profile_state(self, profile_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RegistrySettingsSnapshot:
    had_hosts: bool
    hosts: list[ProfileRecord]
    had_default: bool
    default_host_id: str
    had_schema: bool
    schema_version: int


@dataclass(frozen=True, slots=True)
class MigrationRollback:
    workflow: WorkflowLike
    cache: MigrationCache
    profile_id: str
    account: str
    snapshot: RegistrySettingsSnapshot
    token_attempted: bool
    cache_attempted: bool

    def run(self) -> tuple[Exception, ...]:
        failures: list[Exception] = []
        if self.cache_attempted:
            try:
                self.cache.clear_profile_state(self.profile_id)
            except ROLLBACK_ERRORS as error:
                failures.append(error)
        if self.token_attempted:
            try:
                self.workflow.delete_password(self.account)
            except ROLLBACK_ERRORS as error:
                failures.append(error)
        failures.extend(_restore_registry_settings(self.workflow, self.snapshot))
        return tuple(failures)


def snapshot_registry_settings(
    workflow: WorkflowLike,
    profiles: list[ProfileRecord],
) -> RegistrySettingsSnapshot:
    return RegistrySettingsSnapshot(
        had_hosts="hosts" in workflow.settings,
        hosts=profiles,
        had_default="default_host_id" in workflow.settings,
        default_host_id=workflow.settings.get("default_host_id", ""),
        had_schema="host_schema_version" in workflow.settings,
        schema_version=workflow.settings.get("host_schema_version", 0),
    )


def _restore_registry_settings(
    workflow: WorkflowLike,
    snapshot: RegistrySettingsSnapshot,
) -> tuple[Exception, ...]:
    failures: list[Exception] = []
    values: tuple[tuple[str, bool, list[ProfileRecord] | str | int], ...] = (
        ("hosts", snapshot.had_hosts, snapshot.hosts),
        ("default_host_id", snapshot.had_default, snapshot.default_host_id),
        ("host_schema_version", snapshot.had_schema, snapshot.schema_version),
    )
    for key, existed, value in values:
        try:
            _restore_setting(workflow, key, existed, value)
        except ROLLBACK_ERRORS:
            try:
                _restore_in_memory_setting(workflow, key, existed, value)
            except ROLLBACK_ERRORS as error:
                failures.append(error)
    return tuple(failures)


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


def _restore_in_memory_setting(
    workflow: WorkflowLike,
    key: str,
    existed: bool,
    value: list[ProfileRecord] | str | int,
) -> None:
    settings = workflow.settings
    if not isinstance(settings, dict):
        return
    in_memory_settings = cast(dict[str, object], settings)
    _ = in_memory_settings.pop(key, None)
    if existed:
        _ = in_memory_settings.setdefault(key, value)
