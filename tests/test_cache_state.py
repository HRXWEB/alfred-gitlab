from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import cache_state

PROFILE_A = "a" * 32
PROFILE_B = "b" * 32


class FakeWorkflow:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = data_dir
        self.cache: dict[str, cache_state.CachePayload] = {}
        self.read_errors: dict[str, Exception] = {}

    def datafile(self, name: str) -> str:
        return str(self.data_dir / name)

    def cache_data(
        self,
        name: str,
        value: cache_state.CachePayload | None,
    ) -> None:
        if value is None:
            _ = self.cache.pop(name, None)
            return
        self.cache[name] = value

    def cached_data(
        self,
        name: str,
        data_func: None = None,
        *,
        max_age: int = 60,
    ) -> cache_state.CachePayload | None:
        del data_func, max_age
        error = self.read_errors.get(name)
        if error is not None:
            raise error
        return self.cache.get(name)


def test_profile_project_caches_do_not_overlap(tmp_path: Path) -> None:
    # Given: two cache views over one workflow
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)

    # When: each profile stores a different project list
    assert cache.store_projects(PROFILE_A, "", [{"id": 1}])
    assert cache.store_projects(PROFILE_B, "", [{"id": 2}])

    # Then: each profile reads only its own projects
    assert cache.load_projects(PROFILE_A) == [{"id": 1}]
    assert cache.load_projects(PROFILE_B) == [{"id": 2}]


def test_legacy_projects_migrate_without_removing_legacy_cache(
    tmp_path: Path,
) -> None:
    # Given: a valid v3.1 project cache
    workflow = FakeWorkflow(tmp_path)
    workflow.cache["projects"] = [{"id": 7}]
    cache = cache_state.CacheState(workflow)

    # When: the legacy projects migrate to a profile
    projects = cache.load_legacy_projects()
    assert projects is not None
    cache.migrate_legacy_projects(PROFILE_A, projects)

    # Then: both downgrade and profile-scoped caches remain available
    assert workflow.cache["projects"] == [{"id": 7}]
    assert cache.load_projects(PROFILE_A) == [{"id": 7}]


def test_invalidation_rejects_only_same_profile_generation(
    tmp_path: Path,
) -> None:
    # Given: generation snapshots for two profiles
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    generation_a = cache.current_generation(PROFILE_A)
    generation_b = cache.current_generation(PROFILE_B)

    # When: only profile A is invalidated
    cache.invalidate_projects(PROFILE_A)

    # Then: A's stale write is rejected and B's write remains valid
    assert not cache.store_projects(PROFILE_A, generation_a, [])
    assert cache.store_projects(PROFILE_B, generation_b, [])


def test_refresh_success_atomically_replaces_projects_and_clears_status(
    tmp_path: Path,
) -> None:
    # Given: cached projects and an error status for one profile
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    assert cache.store_projects(PROFILE_A, "", [{"id": 1}])
    cache.store_status(
        PROFILE_A,
        {"ok": False, "category": "HTTPErrorStatus", "http_status": 401},
    )
    generation = cache.current_generation(PROFILE_A)

    # When: a successful refresh publishes against the current generation
    stored = cache.publish_refresh_success(
        PROFILE_A,
        generation,
        [{"id": 2}],
    )

    # Then: the project replacement and status clear are both visible
    assert stored
    assert cache.load_projects(PROFILE_A) == [{"id": 2}]
    assert cache.load_status(PROFILE_A) is None


def test_stale_refresh_success_preserves_projects_and_newer_error_status(
    tmp_path: Path,
) -> None:
    # Given: a refresh snapshot made before profile invalidation
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    stale_generation = cache.current_generation(PROFILE_A)
    cache.invalidate_projects(PROFILE_A)
    current_generation = cache.current_generation(PROFILE_A)
    assert cache.store_projects(PROFILE_A, current_generation, [{"id": 2}])
    newer_status = {
        "ok": False,
        "category": "HTTPErrorStatus",
        "http_status": 503,
    }
    cache.store_status(PROFILE_A, newer_status)

    # When: the stale refresh attempts to publish success
    stored = cache.publish_refresh_success(
        PROFILE_A,
        stale_generation,
        [{"id": 1}],
    )

    # Then: neither part of the newer profile state is changed
    assert not stored
    assert cache.load_projects(PROFILE_A) == [{"id": 2}]
    assert cache.load_status(PROFILE_A) == newer_status


def test_profile_status_caches_do_not_overlap(tmp_path: Path) -> None:
    # Given: distinct failure status for two profiles
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    status_a = {
        "ok": False,
        "category": "HTTPErrorStatus",
        "http_status": 401,
        "message": "HTTP 401",
        "updated_at": 1784880000,
    }
    status_b = {
        "ok": False,
        "category": "ConnectionError",
        "message": "Connection failed",
        "updated_at": 1784880001,
    }

    # When: each profile stores its own status
    cache.store_status(PROFILE_A, status_a)
    cache.store_status(PROFILE_B, status_b)

    # Then: the statuses remain isolated
    assert cache.load_status(PROFILE_A) == status_a
    assert cache.load_status(PROFILE_B) == status_b


@pytest.mark.parametrize(
    "invalid_cache",
    [
        {"id": 1},
        "projects",
        7,
        [{"id": 1}, "invalid-project"],
    ],
)
def test_invalid_project_cache_is_invalidated(
    tmp_path: Path,
    invalid_cache: cache_state.CachePayload,
) -> None:
    # Given: invalid cached project data for profile A
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    key = cache_state.projects_key(PROFILE_A)
    workflow.cache[key] = invalid_cache
    generation = cache.current_generation(PROFILE_A)

    # When: the invalid cache is loaded
    projects = cache.load_projects(PROFILE_A)

    # Then: only that cache is discarded and its generation changes
    assert projects is None
    assert key not in workflow.cache
    assert cache.current_generation(PROFILE_A) != generation


def test_corrupt_project_cache_is_invalidated(tmp_path: Path) -> None:
    # Given: a project cache that cannot be deserialized
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    key = cache_state.projects_key(PROFILE_A)
    workflow.read_errors[key] = pickle.UnpicklingError("invalid cache")
    generation = cache.current_generation(PROFILE_A)

    # When: the corrupt cache is loaded
    projects = cache.load_projects(PROFILE_A)

    # Then: the affected profile is invalidated
    assert projects is None
    assert cache.current_generation(PROFILE_A) != generation


def test_status_storage_omits_unapproved_fields(tmp_path: Path) -> None:
    # Given: a status mapping with sensitive diagnostic fields
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    status = {
        "ok": False,
        "category": "HTTPErrorStatus",
        "http_status": 401,
        "message": "HTTP 401",
        "updated_at": 1784880000,
        "url": "https://gitlab.example.test/api/v4/projects",
        "response_body": "secret response",
        "traceback": "secret traceback",
        "token": "secret token",
    }

    # When: the status is stored
    cache.store_status(PROFILE_A, status)

    # Then: only the approved status fields reach the cache
    assert workflow.cache[cache_state.status_key(PROFILE_A)] == {
        "ok": False,
        "category": "HTTPErrorStatus",
        "http_status": 401,
        "message": "HTTP 401",
        "updated_at": 1784880000,
    }


def test_clear_rejects_writer_with_generation_captured_before_cleanup(
    tmp_path: Path,
) -> None:
    # Given: a writer captured the initial profile generation
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    captured_generation = cache.current_generation(PROFILE_A)
    assert captured_generation == ""

    # When: the profile is cleared before the writer publishes
    cache.clear_profile_state(PROFILE_A)

    # Then: cleanup advances the generation and rejects the stale writer
    assert cache.current_generation(PROFILE_A)
    assert not cache.store_projects(
        PROFILE_A,
        captured_generation,
        [{"id": 1}],
    )


def test_clear_profile_state_preserves_coordination_and_clears_payloads(
    tmp_path: Path,
) -> None:
    # Given: complete cache state for two profiles
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    assert cache.store_projects(PROFILE_A, "", [{"id": 1}])
    assert cache.store_projects(PROFILE_B, "", [{"id": 2}])
    cache.store_status(PROFILE_A, {"ok": True})
    cache.store_status(PROFILE_B, {"ok": True})
    cache.invalidate_projects(PROFILE_A)
    generation_path = Path(cache.generation_path(PROFILE_A))
    lock_path = Path(cache.lock_path(PROFILE_A))
    generation_before = cache.current_generation(PROFILE_A)
    lock_inode_before = lock_path.stat().st_ino
    assert generation_path.exists()
    assert lock_path.exists()

    # When: profile A state is cleared
    cache.clear_profile_state(PROFILE_A)

    # Then: A payloads are gone, coordination persists, and B remains
    assert cache.load_projects(PROFILE_A) is None
    assert cache.load_status(PROFILE_A) is None
    assert generation_path.exists()
    assert cache.current_generation(PROFILE_A) != generation_before
    assert lock_path.exists()
    assert lock_path.stat().st_ino == lock_inode_before
    assert cache.load_projects(PROFILE_B) == [{"id": 2}]
    assert cache.load_status(PROFILE_B) == {"ok": True}


def test_retired_profile_rejects_refresh_started_after_removal(
    tmp_path: Path,
) -> None:
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))

    cache.retire_profile(PROFILE_A)

    with pytest.raises(cache_state.RetiredProfileError):
        cache.begin_refresh(PROFILE_A)


def test_restored_profile_can_refresh_after_failed_removal(tmp_path: Path) -> None:
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    cache.retire_profile(PROFILE_A)

    cache.restore_profile(PROFILE_A)

    assert cache.begin_refresh(PROFILE_A)


@pytest.mark.parametrize(
    "profile_id",
    ["", "A" * 32, "a" * 31, "a" * 33, "../projects", "g" * 32],
)
def test_invalid_profile_id_is_rejected(
    tmp_path: Path,
    profile_id: str,
) -> None:
    # Given: a cache service and an unsafe profile identifier
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))

    # When / Then: the identifier is rejected before path construction
    with pytest.raises(cache_state.InvalidProfileIdError):
        _ = cache.current_generation(profile_id)
