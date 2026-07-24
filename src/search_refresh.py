from __future__ import annotations

import sys
from typing import Final, Protocol

from cache_records import projects_key
from host_values import ProfileRecord
from workflow.background import is_running, run_in_background

PROJECT_CACHE_MAX_AGE: Final = 3600


class FreshnessWorkflow(Protocol):
    def cached_data_fresh(self, name: str, max_age: int) -> bool: ...

    def workflowfile(self, name: str) -> str: ...


def refresh_stale_profile(
    workflow: FreshnessWorkflow,
    profile: ProfileRecord,
) -> None:
    project_key = projects_key(profile["id"])
    update_name = f"update-{profile['id']}"
    if workflow.cached_data_fresh(
        project_key,
        max_age=PROJECT_CACHE_MAX_AGE,
    ):
        return
    if is_running(update_name):
        return
    _ = run_in_background(
        update_name,
        [
            sys.executable,
            workflow.workflowfile("update.py"),
            "--host-id",
            profile["id"],
        ],
    )
