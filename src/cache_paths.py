from __future__ import annotations

import os
import uuid
from typing import Protocol

from cache_records import projects_key


class CachePathWorkflow(Protocol):
    def datafile(self, name: str) -> str: ...


def generation_path(workflow: CachePathWorkflow, profile_id: str) -> str:
    return workflow.datafile(f"{projects_key(profile_id)}.generation")


def lock_path(workflow: CachePathWorkflow, profile_id: str) -> str:
    return workflow.datafile(f"{projects_key(profile_id)}.lock")


def retired_path(workflow: CachePathWorkflow, profile_id: str) -> str:
    return workflow.datafile(f"{projects_key(profile_id)}.retired")


def current_generation(workflow: CachePathWorkflow, profile_id: str) -> str:
    try:
        with open(generation_path(workflow, profile_id), encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        return ""


def rotate_generation(workflow: CachePathWorkflow, profile_id: str) -> str:
    path = generation_path(workflow, profile_id)
    temporary_path = f"{path}.{uuid.uuid4().hex}"
    generation = uuid.uuid4().hex
    with open(temporary_path, "w", encoding="utf-8") as generation_file:
        _ = generation_file.write(generation)
    os.replace(temporary_path, path)
    return generation
