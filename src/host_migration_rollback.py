from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from host_registry_state import (
    ROLLBACK_ERRORS,
    RegistrySettingsSnapshot,
    WorkflowLike,
    restore_registry_settings,
)


class MigrationCache(Protocol):
    def clear_profile_state(self, profile_id: str) -> None: ...


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
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
        failures.extend(restore_registry_settings(self.workflow, self.snapshot))
        return tuple(failures)
