from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from cache_records import JSONValue, Project, Projects, StatusRecord
from host_values import ProfileRecord
from search_refresh import FreshnessWorkflow, refresh_stale_profile
from workflow import ICON_WARNING


class SearchWorkflow(FreshnessWorkflow, Protocol):
    rerun: float

    def add_item(
        self,
        title: str,
        subtitle: str | None = None,
        **kwargs: JSONValue,
    ) -> None: ...


class SearchCache(Protocol):
    def load_projects(self, profile_id: str) -> Projects | None: ...

    def load_status(self, profile_id: str) -> StatusRecord | None: ...


def search_for_project(project: Project) -> str:
    return " ".join(
        (
            str(project["name_with_namespace"]),
            str(project["path_with_namespace"]),
            str(project.get("_host_name", "")),
        )
    )


def aggregate_projects(
    workflow: SearchWorkflow,
    profiles: Sequence[ProfileRecord],
    cache: SearchCache,
) -> Projects:
    aggregate: Projects = []
    for profile in profiles:
        refreshing = refresh_stale_profile(workflow, profile)
        cached_projects = cache.load_projects(profile["id"])
        if refreshing and not cached_projects:
            workflow.rerun = 0.5
        projects = cached_projects or []
        for project in projects:
            retained = dict(project)
            retained["_host_id"] = profile["id"]
            retained["_host_name"] = profile["name"]
            retained["_api_url"] = profile["api_url"]
            retained["_alfred_uid"] = f"{profile['id']}:{project.get('id', '')}"
            aggregate.append(retained)

        status = cache.load_status(profile["id"])
        if status is not None and status.get("ok") is False:
            http_status = status.get("http_status")
            subtitle = (
                f"HTTP {http_status}"
                if isinstance(http_status, int) and not isinstance(http_status, bool)
                else "Refresh failed"
            )
            workflow.add_item(
                f"{profile['name']} refresh failed",
                subtitle,
                valid=False,
                icon=ICON_WARNING,
            )
    return aggregate
