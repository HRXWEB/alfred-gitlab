from __future__ import annotations

import fcntl
import os
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import cache_paths
from cache_records import (
    CACHE_ERRORS,
    PROFILE_ID,
    CachePayload,
    CacheValue,
    InvalidProfileIdError,
    InvalidStatusError,
    JSONScalar,
    JSONValue,
    Project,
    Projects,
    StatusRecord,
    projects_key,
    sanitized_status,
    status_key,
    status_payload,
    validate_profile_id,
)

CACHE_STATE_FAILURES = (OSError, *CACHE_ERRORS)
__all__ = [
    "CACHE_ERRORS",
    "PROFILE_ID",
    "CachePayload",
    "CacheState",
    "CacheValue",
    "CacheWorkflow",
    "InvalidProfileIdError",
    "InvalidStatusError",
    "JSONScalar",
    "JSONValue",
    "Project",
    "Projects",
    "StatusRecord",
    "projects_key",
    "status_key",
    "validate_profile_id",
]


class CacheWorkflow(Protocol):
    def datafile(self, name: str) -> str: ...

    def cache_data(
        self,
        name: str,
        value: CacheValue | None,
    ) -> None: ...

    def cached_data(
        self,
        name: str,
        data_func: None = None,
        *,
        max_age: int = 60,
    ) -> CachePayload | None: ...


class RetiredProfileError(RuntimeError):
    pass


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class CacheState:
    workflow: CacheWorkflow

    def load_legacy_projects(self) -> Projects | None:
        try:
            cached = self.workflow.cached_data("projects", None, max_age=0)
        except CACHE_ERRORS:
            return None
        if not isinstance(cached, list):
            return None
        projects: Projects = []
        for project in cached:
            if not isinstance(project, dict):
                return None
            projects.append(project)
        return projects

    def migrate_legacy_projects(
        self,
        profile_id: str,
        projects: Sequence[Mapping[str, JSONValue]],
    ) -> bool:
        return self.store_projects(
            profile_id,
            self.current_generation(profile_id),
            projects,
        )

    def generation_path(self, profile_id: str) -> str:
        return cache_paths.generation_path(self.workflow, profile_id)

    def lock_path(self, profile_id: str) -> str:
        return cache_paths.lock_path(self.workflow, profile_id)

    def retired_path(self, profile_id: str) -> str:
        return cache_paths.retired_path(self.workflow, profile_id)

    def current_generation(self, profile_id: str) -> str:
        return cache_paths.current_generation(self.workflow, profile_id)

    def load_projects(self, profile_id: str) -> Projects | None:
        with self._exclusive_lock(profile_id):
            return self._load_projects_locked(profile_id)

    def _load_projects_locked(self, profile_id: str) -> Projects | None:
        key = projects_key(profile_id)
        try:
            cached = self.workflow.cached_data(key, None, max_age=0)
        except CACHE_ERRORS:
            self._invalidate_projects_locked(profile_id)
            return None
        if cached is None:
            return None
        if not isinstance(cached, list):
            self._invalidate_projects_locked(profile_id)
            return None

        projects: Projects = []
        for project in cached:
            if not isinstance(project, dict):
                self._invalidate_projects_locked(profile_id)
                return None
            projects.append(project)
        return projects

    def invalidate_projects(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            self._invalidate_projects_locked(profile_id)

    def _invalidate_projects_locked(self, profile_id: str) -> None:
        _ = self._rotate_generation(profile_id)
        self.workflow.cache_data(projects_key(profile_id), None)

    def begin_refresh(self, profile_id: str) -> str:
        with self._exclusive_lock(profile_id):
            if os.path.exists(self.retired_path(profile_id)):
                raise RetiredProfileError(profile_id)
            return self._rotate_generation(profile_id)

    def store_projects(
        self,
        profile_id: str,
        generation: str,
        projects: Sequence[Mapping[str, JSONValue]],
    ) -> bool:
        with self._exclusive_lock(profile_id):
            if self.current_generation(profile_id) != generation:
                return False
            self.workflow.cache_data(
                projects_key(profile_id),
                [dict(project) for project in projects],
            )
            return True

    def publish_refresh_success(
        self,
        profile_id: str,
        generation: str,
        projects: Sequence[Mapping[str, JSONValue]],
    ) -> bool:
        with self._exclusive_lock(profile_id):
            if self.current_generation(profile_id) != generation:
                return False
            project_key = projects_key(profile_id)
            profile_status_key = status_key(profile_id)
            old_projects = self.workflow.cached_data(
                project_key,
                None,
                max_age=0,
            )
            old_status = self.workflow.cached_data(
                profile_status_key,
                None,
                max_age=0,
            )
            try:
                self.workflow.cache_data(
                    project_key,
                    [dict(project) for project in projects],
                )
                self.workflow.cache_data(profile_status_key, None)
            except Exception:  #noqa: BROAD_EXCEPT_OK
                self.workflow.cache_data(project_key, old_projects)
                self.workflow.cache_data(profile_status_key, old_status)
                raise
            return True

    def load_status(self, profile_id: str) -> StatusRecord | None:
        with self._exclusive_lock(profile_id):
            return self._load_status_locked(profile_id)

    def _load_status_locked(self, profile_id: str) -> StatusRecord | None:
        key = status_key(profile_id)
        try:
            cached = self.workflow.cached_data(key, None, max_age=0)
        except CACHE_ERRORS:
            self.workflow.cache_data(key, None)
            return None
        if cached is None:
            return None
        if not isinstance(cached, dict):
            self.workflow.cache_data(key, None)
            return None
        try:
            return sanitized_status(cached)
        except InvalidStatusError:
            self.workflow.cache_data(key, None)
            return None

    def store_status(
        self,
        profile_id: str,
        status: Mapping[str, JSONValue],
    ) -> None:
        cached = status_payload(status)
        with self._exclusive_lock(profile_id):
            self.workflow.cache_data(status_key(profile_id), cached)

    def publish_refresh_failure(
        self,
        profile_id: str,
        generation: str,
        status: Mapping[str, JSONValue],
    ) -> bool:
        cached = status_payload(status)
        with self._exclusive_lock(profile_id):
            if self.current_generation(profile_id) != generation:
                return False
            self.workflow.cache_data(status_key(profile_id), cached)
            return True

    def clear_profile_state(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            _ = self._rotate_generation(profile_id)
            self.workflow.cache_data(projects_key(profile_id), None)
            self.workflow.cache_data(status_key(profile_id), None)

    def retire_profile(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            retired_path = self.retired_path(profile_id)
            with open(retired_path, "w", encoding="utf-8"):
                pass
            try:
                _ = self._rotate_generation(profile_id)
                self.workflow.cache_data(projects_key(profile_id), None)
                self.workflow.cache_data(status_key(profile_id), None)
            except CACHE_STATE_FAILURES:
                try:
                    os.unlink(retired_path)
                except FileNotFoundError as error:
                    _ = error
                raise

    def restore_profile(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            try:
                os.unlink(self.retired_path(profile_id))
            except FileNotFoundError as error:
                _ = error
            _ = self._rotate_generation(profile_id)

    def _rotate_generation(self, profile_id: str) -> str:
        return cache_paths.rotate_generation(self.workflow, profile_id)

    @contextmanager
    def _exclusive_lock(self, profile_id: str) -> Generator[None, None, None]:
        lock_path = self.lock_path(profile_id)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "a", encoding="utf-8") as lock_file:
            _ = fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield
