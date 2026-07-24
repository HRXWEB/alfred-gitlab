from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Union

import mureq
from cache_records import Projects
from cache_state import CacheState, RetiredProfileError
from host_values import HostProfile
from hosts import token_account
from refresh_runtime import (
    AlfredWorkflowFacade,
    RefreshWorkers,
    RefreshWorkflow,
    default_workers,
)
from workflow import PasswordNotFound, Workflow

MAX_REFRESH_WORKERS = 4
PROFILE_FETCH_FAILURES = (PasswordNotFound, mureq.HTTPException)


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class RefreshSuccess:
    host: str
    project_count: int
    ok: Literal[True] = True


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class RefreshFailure:
    host: str
    category: str
    http_status: int | None
    ok: Literal[False] = False


RefreshResult = Union[RefreshSuccess, RefreshFailure]  # noqa: UP007


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
    try:
        generation = cache.begin_refresh(profile.id)
    except RetiredProfileError:
        return RefreshFailure(
            host=profile.name,
            category="RetiredProfile",
            http_status=None,
        )
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
        _ = cache.publish_refresh_failure(
            profile.id,
            generation,
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
        if isinstance(result, RefreshSuccess):
            refreshed += 1
        else:
            failed += 1
    return f"{refreshed} refreshed, {failed} failed"


def _refresh_in_worker(
    profile: HostProfile,
    workers: RefreshWorkers,
) -> RefreshResult:
    workflow = workers.workflow_factory()
    cache = workers.cache_factory(workflow)
    return refresh_profile(workflow, profile, cache)


def _default_workers() -> RefreshWorkers:
    return default_workers()


def _exit_code(result: RefreshResult) -> int:
    return 0 if isinstance(result, RefreshSuccess) else 1


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
