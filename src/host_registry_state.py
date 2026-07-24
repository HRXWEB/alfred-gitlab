from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, cast, overload

from host_values import ProfileRecord
from workflow import KeychainError, PasswordNotFound
from workflow.util import AcquisitionError

ROLLBACK_ERRORS = (OSError, AcquisitionError, KeychainError, PasswordNotFound)


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


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class RegistrySettingsSnapshot:
    had_hosts: bool
    hosts: list[ProfileRecord]
    had_default: bool
    default_host_id: str
    had_schema: bool
    schema_version: int


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


def restore_registry_settings(
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
    in_memory_settings = cast(dict[str, list[ProfileRecord] | str | int], settings)
    _ = in_memory_settings.pop(key, None)
    if existed:
        _ = in_memory_settings.setdefault(key, value)
