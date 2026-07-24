from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal, Protocol, assert_never, overload

import mureq
from cache_state import (
    CachePayload,
    CacheState,
    CacheValue,
    CacheWorkflow,
    Projects,
)
from host_values import HostProfile, ProfileRecord
from hosts import token_account
from workflow import PasswordNotFound, Workflow

MAX_REFRESH_WORKERS = 4
PROFILE_FETCH_FAILURES = (Exception,)


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


@dataclass(frozen=True, slots=True)
class RefreshSuccess:
    host: str
    project_count: int
    ok: Literal[True] = True


@dataclass(frozen=True, slots=True)
class RefreshFailure:
    host: str
    category: str
    http_status: int | None
    ok: Literal[False] = False


RefreshResult = RefreshSuccess | RefreshFailure
WorkflowFactory = Callable[[], RefreshWorkflow]
CacheFactory = Callable[[RefreshWorkflow], CacheState]


@dataclass(frozen=True, slots=True)
class RefreshWorkers:
    workflow_factory: WorkflowFactory
    cache_factory: CacheFactory


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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


def get_projects(api_key: str, url: str) -> Projects:
    projects: Projects = []
    page: str | int = 1
    while page:
        response = mureq.get(
            url,
            headers={"PRIVATE-TOKEN": api_key},
            params={"per_page": 100, "page": page, "membership": "true"},
        )
        response.raise_for_status()
        projects.extend(response.json())
        page = response.headers.get("X-Next-Page", "")
    return projects


def refresh_profile(
    workflow: RefreshWorkflow,
    profile: HostProfile,
    cache: CacheState,
) -> RefreshResult:
    generation = cache.current_generation(profile.id)
    try:
        api_key = workflow.get_password(token_account(profile.id))
        projects = get_projects(api_key, profile.api_url)
    except PROFILE_FETCH_FAILURES as error:
        status = getattr(error, "status_code", None)
        http_status = (
            status if isinstance(status, int) and not isinstance(status, bool) else None
        )
        failure = RefreshFailure(
            host=profile.name,
            category=type(error).__name__,
            http_status=http_status,
        )
        cache.store_status(
            profile.id,
            {
                "ok": failure.ok,
                "category": failure.category,
                "http_status": failure.http_status,
            },
        )
        return failure

    if not cache.publish_refresh_success(
        profile.id,
        generation,
        projects,
    ):
        return RefreshFailure(
            host=profile.name,
            category="OutdatedProfileGeneration",
            http_status=None,
        )
    return RefreshSuccess(host=profile.name, project_count=len(projects))


def refresh_profiles(
    profiles: Sequence[HostProfile],
    workers: RefreshWorkers,
    max_workers: int = MAX_REFRESH_WORKERS,
) -> list[RefreshResult]:
    if not profiles:
        return []
    worker_count = min(MAX_REFRESH_WORKERS, max_workers, len(profiles))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_refresh_in_worker, profile, workers)
            for profile in profiles
        ]
        return [future.result() for future in futures]


def refresh_summary(results: Sequence[RefreshResult]) -> str:
    refreshed = 0
    failed = 0
    for result in results:
        match result:
            case RefreshSuccess():
                refreshed += 1
            case RefreshFailure():
                failed += 1
            case unreachable:
                assert_never(unreachable)
    return f"{refreshed} refreshed, {failed} failed"


def _refresh_in_worker(
    profile: HostProfile,
    workers: RefreshWorkers,
) -> RefreshResult:
    workflow = workers.workflow_factory()
    cache = workers.cache_factory(workflow)
    return refresh_profile(workflow, profile, cache)


def _default_workers() -> RefreshWorkers:
    return RefreshWorkers(
        workflow_factory=lambda: AlfredWorkflowFacade(Workflow()),
        cache_factory=CacheState,
    )


def _exit_code(result: RefreshResult) -> int:
    match result:
        case RefreshSuccess():
            return 0
        case RefreshFailure():
            return 1
        case unreachable:
            assert_never(unreachable)


def main(workflow: RefreshWorkflow) -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--host-id")
    selection.add_argument("--all", action="store_true", dest="all_profiles")
    args = parser.parse_args(workflow.args)
    profiles = tuple(
        HostProfile.from_record(record) for record in workflow.settings.get("hosts", [])
    )

    if args.all_profiles:
        results = refresh_profiles(profiles, _default_workers())
        print(refresh_summary(results))
        return 0

    if args.host_id is not None:
        profile = next(
            (candidate for candidate in profiles if candidate.id == args.host_id),
            None,
        )
        if profile is None:
            return 1
    else:
        default_id = workflow.settings.get("default_host_id", "")
        profile = next(
            (candidate for candidate in profiles if candidate.id == default_id),
            profiles[0] if profiles else None,
        )
        if profile is None:
            return 0

    return _exit_code(refresh_profile(workflow, profile, CacheState(workflow)))


if __name__ == "__main__":
    wf = Workflow()
    facade = AlfredWorkflowFacade(wf)
    raise SystemExit(wf.run(lambda _workflow: main(facade)))
