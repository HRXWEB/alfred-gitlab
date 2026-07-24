from __future__ import annotations

import fcntl
import os
import pickle
import re
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, Protocol, TypedDict

PROFILE_ID: Final = re.compile(r"^[0-9a-f]{32}$")
CACHE_ERRORS: Final = (
    pickle.UnpicklingError,
    EOFError,
    AttributeError,
    ImportError,
    IndexError,
    OverflowError,
    UnicodeDecodeError,
    ValueError,
)

JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
Project = dict[str, JSONValue]
Projects = list[Project]
CacheValue = JSONValue


class StatusRecord(TypedDict, total=False):
    ok: bool
    category: str
    http_status: int | None
    message: str
    updated_at: int


CachePayload = CacheValue


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


@dataclass(frozen=True, slots=True)
class InvalidProfileIdError(ValueError):
    profile_id: str

    def __str__(self) -> str:
        return "Invalid GitLab host profile ID"


@dataclass(frozen=True, slots=True)
class InvalidStatusError(ValueError):
    field: str

    def __str__(self) -> str:
        return f"Invalid GitLab cache status field: {self.field}"


def validate_profile_id(profile_id: str) -> str:
    if PROFILE_ID.fullmatch(profile_id) is None:
        raise InvalidProfileIdError(profile_id)
    return profile_id


def projects_key(profile_id: str) -> str:
    return f"projects-{validate_profile_id(profile_id)}"


def status_key(profile_id: str) -> str:
    return f"status-{validate_profile_id(profile_id)}"


@dataclass(frozen=True, slots=True)
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
        return self.workflow.datafile(f"{projects_key(profile_id)}.generation")

    def lock_path(self, profile_id: str) -> str:
        return self.workflow.datafile(f"{projects_key(profile_id)}.lock")

    def current_generation(self, profile_id: str) -> str:
        try:
            with open(
                self.generation_path(profile_id),
                encoding="utf-8",
            ) as generation_file:
                return generation_file.read()
        except FileNotFoundError:
            return ""

    def load_projects(self, profile_id: str) -> Projects | None:
        key = projects_key(profile_id)
        try:
            cached = self.workflow.cached_data(key, None, max_age=0)
        except CACHE_ERRORS:
            self.invalidate_projects(profile_id)
            return None
        if cached is None:
            return None
        if not isinstance(cached, list):
            self.invalidate_projects(profile_id)
            return None

        projects: Projects = []
        for project in cached:
            if not isinstance(project, dict):
                self.invalidate_projects(profile_id)
                return None
            projects.append(project)
        return projects

    def invalidate_projects(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            self._rotate_generation(profile_id)
            self.workflow.cache_data(projects_key(profile_id), None)

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
            self.workflow.cache_data(
                projects_key(profile_id),
                [dict(project) for project in projects],
            )
            self.workflow.cache_data(status_key(profile_id), None)
            return True

    def load_status(self, profile_id: str) -> StatusRecord | None:
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
            return _sanitized_status(cached)
        except InvalidStatusError:
            self.workflow.cache_data(key, None)
            return None

    def store_status(
        self,
        profile_id: str,
        status: Mapping[str, JSONValue],
    ) -> None:
        sanitized = _sanitized_status(status)
        cached: dict[str, JSONValue] = {}
        if "ok" in sanitized:
            cached["ok"] = sanitized["ok"]
        if "category" in sanitized:
            cached["category"] = sanitized["category"]
        if "http_status" in sanitized:
            cached["http_status"] = sanitized["http_status"]
        if "message" in sanitized:
            cached["message"] = sanitized["message"]
        if "updated_at" in sanitized:
            cached["updated_at"] = sanitized["updated_at"]
        self.workflow.cache_data(
            status_key(profile_id),
            cached,
        )

    def clear_profile_state(self, profile_id: str) -> None:
        with self._exclusive_lock(profile_id):
            self._rotate_generation(profile_id)
            self.workflow.cache_data(projects_key(profile_id), None)
            self.workflow.cache_data(status_key(profile_id), None)

    def _rotate_generation(self, profile_id: str) -> None:
        generation_path = self.generation_path(profile_id)
        temporary_path = f"{generation_path}.{uuid.uuid4().hex}"
        with open(temporary_path, "w", encoding="utf-8") as generation_file:
            _ = generation_file.write(uuid.uuid4().hex)
        os.replace(temporary_path, generation_path)

    @contextmanager
    def _exclusive_lock(self, profile_id: str) -> Generator[None, None, None]:
        lock_path = self.lock_path(profile_id)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "a", encoding="utf-8") as lock_file:
            _ = fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield


def _sanitized_status(
    status: Mapping[str, JSONValue],
) -> StatusRecord:
    sanitized: StatusRecord = {}
    if "ok" in status:
        ok = status["ok"]
        if not isinstance(ok, bool):
            raise InvalidStatusError("ok")
        sanitized["ok"] = ok
    if "category" in status:
        category = status["category"]
        if not isinstance(category, str):
            raise InvalidStatusError("category")
        sanitized["category"] = category
    if "http_status" in status:
        http_status = status["http_status"]
        if http_status is not None and (
            not isinstance(http_status, int) or isinstance(http_status, bool)
        ):
            raise InvalidStatusError("http_status")
        sanitized["http_status"] = http_status
    if "message" in status:
        message = status["message"]
        if not isinstance(message, str):
            raise InvalidStatusError("message")
        sanitized["message"] = message
    if "updated_at" in status:
        updated_at = status["updated_at"]
        if not isinstance(updated_at, int) or isinstance(updated_at, bool):
            raise InvalidStatusError("updated_at")
        sanitized["updated_at"] = updated_at
    return sanitized
