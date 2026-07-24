from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, overload

from cache_records import CachePayload, CacheValue
from cache_state import CacheState, CacheWorkflow
from host_values import ProfileRecord
from workflow import PasswordNotFound, Workflow


class RefreshSettings(Protocol):
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


class RefreshWorkflow(CacheWorkflow, Protocol):
    @property
    def settings(self) -> RefreshSettings: ...

    @property
    def args(self) -> Sequence[str]: ...

    def get_password(self, account: str) -> str: ...


WorkflowFactory = Callable[[], RefreshWorkflow]
CacheFactory = Callable[[RefreshWorkflow], CacheState]


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class RefreshWorkers:
    workflow_factory: WorkflowFactory
    cache_factory: CacheFactory


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class AlfredSettingsFacade:
    workflow: Workflow

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

    def get(
        self,
        key: Literal["hosts", "default_host_id"],
        default: list[ProfileRecord] | str,
    ) -> list[ProfileRecord] | str:
        return self.workflow.settings.get(key, default)


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class AlfredWorkflowFacade:
    workflow: Workflow
    settings: RefreshSettings = field(init=False)
    args: Sequence[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settings",
            AlfredSettingsFacade(self.workflow),
        )
        object.__setattr__(
            self,
            "args",
            tuple(
                argument for argument in self.workflow.args if isinstance(argument, str)
            ),
        )

    def get_password(self, account: str) -> str:
        password = self.workflow.get_password(account)
        if not isinstance(password, str):
            raise PasswordNotFound
        return password

    def datafile(self, name: str) -> str:
        path = self.workflow.datafile(name)
        return path if isinstance(path, str) else str(path)

    def cache_data(
        self,
        name: str,
        value: CacheValue | None,
    ) -> None:
        self.workflow.cache_data(name, value)

    def cached_data(
        self,
        name: str,
        data_func: None = None,
        *,
        max_age: int = 60,
    ) -> CachePayload | None:
        return self.workflow.cached_data(name, data_func, max_age=max_age)


def default_workers() -> RefreshWorkers:
    return RefreshWorkers(
        workflow_factory=lambda: AlfredWorkflowFacade(Workflow()),
        cache_factory=CacheState,
    )
