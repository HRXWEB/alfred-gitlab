from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlunsplit

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
    valid_api_url,
)


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
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

    def invalidate_identity(self, profile_id: str) -> None: ...

    def retire_profile(self, profile_id: str) -> None: ...

    def restore_profile(self, profile_id: str) -> None: ...


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


def set_default_host(workflow: WorkflowLike, profile_id: str) -> str:
    selected = HostRegistry(workflow).set_default(profile_id)
    return f"Default host set to {selected.name}"


def render_default_host_list(
    workflow: HostCommandWorkflow,
    profiles: Sequence[ProfileRecord],
    default_profile_id: str,
) -> None:
    for profile in sorted(
        profiles,
        key=lambda profile: profile["id"] != default_profile_id,
    ):
        is_default = profile["id"] == default_profile_id
        display_url = _display_api_url(profile["api_url"])
        workflow.add_item(
            profile["name"],
            (
                f"Current default · {display_url}"
                if is_default
                else f"Set as default · {display_url}"
            ),
            **(
                {"valid": False}
                if is_default
                else {"arg": profile["id"], "valid": True}
            ),
        )


def _display_api_url(api_url: str) -> str:
    parsed = valid_api_url(api_url)
    if parsed is None:
        return "GitLab API URL unavailable"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


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
            invalidate=lambda _workflow, profile_id: _invalidate_profile_caches(
                cache, profile_id
            ),
            cleanup=lambda _workflow, profile_id: cache.retire_profile(profile_id),
            restore=lambda _workflow, profile_id: cache.restore_profile(profile_id),
        ),
    )


def _invalidate_profile_caches(
    cache: HostCommandCache,
    profile_id: str,
) -> None:
    cache.invalidate_projects(profile_id)
    cache.invalidate_identity(profile_id)
