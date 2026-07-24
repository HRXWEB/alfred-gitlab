from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cache_records import JSONValue, Projects, StatusRecord
from host_registry import (
    HostRegistry,
    RegistryCallbacks,
    WorkflowLike,
)
from host_values import (
    ProfileRecord,
    derive_host_name,
    parse_host_add,
)


@dataclass(frozen=True, slots=True)
class HostAddResult:
    message: str
    uses_http: bool


class HostCommandWorkflow(WorkflowLike, Protocol):
    def add_item(
        self,
        title: str,
        subtitle: str | None = None,
        **kwargs: JSONValue,
    ) -> None: ...


class HostCommandCache(Protocol):
    def load_projects(self, profile_id: str) -> Projects | None: ...

    def load_status(self, profile_id: str) -> StatusRecord | None: ...

    def invalidate_projects(self, profile_id: str) -> None: ...

    def clear_profile_state(self, profile_id: str) -> None: ...


def add_host(
    workflow: WorkflowLike,
    cache: HostCommandCache,
    argument: str,
) -> HostAddResult:
    draft = parse_host_add(argument)
    resolved_name = (
        derive_host_name(draft.api_url) if draft.name is None else draft.name
    )
    existed = any(
        profile.name == resolved_name for profile in HostRegistry(workflow).profiles()
    )
    registry = _registry(workflow, cache)
    profile = registry.add_or_update(draft)
    action = "Updated" if existed else "Added"
    return HostAddResult(
        message=f"{action} {profile.name}",
        uses_http=profile.api_url.startswith("http://"),
    )


def remove_host(
    workflow: WorkflowLike,
    cache: HostCommandCache,
    name: str,
) -> str:
    removed = _registry(workflow, cache).remove(name)
    return f"Removed {removed.name}"


def render_host_list(
    workflow: HostCommandWorkflow,
    profiles: Sequence[ProfileRecord],
    cache: HostCommandCache,
) -> None:
    for profile in profiles:
        projects = cache.load_projects(profile["id"]) or []
        status = cache.load_status(profile["id"])
        state = "Ready"
        if status is not None and status.get("ok") is False:
            http_status = status.get("http_status")
            state = (
                f"Refresh failed (HTTP {http_status})"
                if isinstance(http_status, int) and not isinstance(http_status, bool)
                else "Refresh failed"
            )
        workflow.add_item(
            profile["name"],
            (f"{profile['api_url']} · {len(projects)} cached · {state}"),
            valid=False,
        )


def _registry(
    workflow: WorkflowLike,
    cache: HostCommandCache,
) -> HostRegistry:
    return HostRegistry(
        workflow,
        RegistryCallbacks(
            invalidate=lambda _workflow, profile_id: cache.invalidate_projects(
                profile_id
            ),
            cleanup=lambda _workflow, profile_id: cache.clear_profile_state(profile_id),
        ),
    )
