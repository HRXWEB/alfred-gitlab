import sys
import threading
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import cache_state

PROFILE_ID = "a" * 32


class CacheWriteFailure(Exception):
    pass


class FakeWorkflow:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache: dict[str, cache_state.CachePayload] = {}
        self.fail_key: str | None = None
        self.after_project_write = None

    def datafile(self, name: str) -> str:
        return str(self.data_dir / name)

    def cache_data(
        self,
        name: str,
        value: cache_state.CachePayload | None,
    ) -> None:
        should_fail = name == self.fail_key
        if value is None:
            self.cache.pop(name, None)
        else:
            self.cache[name] = value
        if (
            name == cache_state.projects_key(PROFILE_ID)
            and self.after_project_write is not None
        ):
            self.after_project_write()
        if should_fail:
            self.fail_key = None
            raise CacheWriteFailure

    def cached_data(
        self,
        name: str,
        data_func: None = None,
        *,
        max_age: int = 60,
    ) -> cache_state.CachePayload | None:
        del data_func, max_age
        return self.cache.get(name)


def test_later_attempt_rejects_earlier_success(
    tmp_path: Path,
) -> None:
    # Given: two refresh attempts for the same unchanged profile
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    earlier = cache.begin_refresh(PROFILE_ID)
    later = cache.begin_refresh(PROFILE_ID)

    # When: the earlier and later success results try to publish
    earlier_stored = cache.publish_refresh_success(
        PROFILE_ID,
        earlier,
        [{"id": 1}],
    )
    later_stored = cache.publish_refresh_success(
        PROFILE_ID,
        later,
        [{"id": 2}],
    )

    # Then: only the latest non-empty attempt generation wins
    assert earlier
    assert later
    assert earlier != later
    assert not earlier_stored
    assert later_stored
    assert cache.load_projects(PROFILE_ID) == [{"id": 2}]


def test_begin_refresh_preserves_existing_projects_and_status(
    tmp_path: Path,
) -> None:
    # Given: cached state from a previous refresh
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    assert cache.store_projects(PROFILE_ID, "", [{"id": 1}])
    old_status = {"ok": False, "category": "OldFailure"}
    cache.store_status(PROFILE_ID, old_status)

    # When: a new refresh attempt begins
    generation = cache.begin_refresh(PROFILE_ID)

    # Then: only its attempt identity changes
    assert generation
    assert cache.load_projects(PROFILE_ID) == [{"id": 1}]
    assert cache.load_status(PROFILE_ID) == old_status


def test_later_attempt_rejects_earlier_failure(
    tmp_path: Path,
) -> None:
    # Given: two refresh attempts for the same unchanged profile
    cache = cache_state.CacheState(FakeWorkflow(tmp_path))
    earlier = cache.begin_refresh(PROFILE_ID)
    later = cache.begin_refresh(PROFILE_ID)
    earlier_status = {"ok": False, "category": "EarlierFailure"}
    later_status = {"ok": False, "category": "LatestFailure"}

    # When: the earlier and later failures try to publish
    earlier_stored = cache.publish_refresh_failure(
        PROFILE_ID,
        earlier,
        earlier_status,
    )
    later_stored = cache.publish_refresh_failure(
        PROFILE_ID,
        later,
        later_status,
    )

    # Then: only the latest attempt status becomes visible
    assert not earlier_stored
    assert later_stored
    assert cache.load_status(PROFILE_ID) == later_status


@pytest.mark.parametrize(
    "failed_key",
    [
        cache_state.projects_key(PROFILE_ID),
        cache_state.status_key(PROFILE_ID),
    ],
)
def test_success_publication_rolls_back_if_cache_write_fails(
    tmp_path: Path,
    failed_key: str,
) -> None:
    # Given: old state and an injected failure in either publication write
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    assert cache.store_projects(PROFILE_ID, "", [{"id": 1}])
    old_status = {"ok": False, "category": "OldFailure"}
    cache.store_status(PROFILE_ID, old_status)
    workflow.fail_key = failed_key

    # When / Then: the write error propagates without changing the pair
    with pytest.raises(CacheWriteFailure):
        cache.publish_refresh_success(PROFILE_ID, "", [{"id": 2}])
    assert cache.load_projects(PROFILE_ID) == [{"id": 1}]
    assert cache.load_status(PROFILE_ID) == old_status


def test_reader_cannot_observe_mid_publication_pair(
    tmp_path: Path,
) -> None:
    # Given: publication paused after its project write
    workflow = FakeWorkflow(tmp_path)
    cache = cache_state.CacheState(workflow)
    assert cache.store_projects(PROFILE_ID, "", [{"id": 1}])
    cache.store_status(PROFILE_ID, {"ok": False, "category": "OldFailure"})
    first_write_done = threading.Event()
    continue_publication = threading.Event()
    reader_done = threading.Event()
    reader_state = []

    def pause_after_project_write() -> None:
        first_write_done.set()
        assert continue_publication.wait(timeout=2)

    workflow.after_project_write = pause_after_project_write

    def publish() -> None:
        cache.publish_refresh_success(PROFILE_ID, "", [{"id": 2}])

    def read() -> None:
        reader_state.append(
            (
                cache.load_projects(PROFILE_ID),
                cache.load_status(PROFILE_ID),
            )
        )
        reader_done.set()

    publisher = threading.Thread(target=publish)
    publisher.start()
    assert first_write_done.wait(timeout=2)
    reader = threading.Thread(target=read)
    reader.start()

    # When: the reader attempts to load during the paused transaction
    blocked_until_commit = not reader_done.wait(timeout=0.05)
    continue_publication.set()
    publisher.join(timeout=2)
    reader.join(timeout=2)

    # Then: it blocks and observes only the committed pair
    assert blocked_until_commit
    assert not publisher.is_alive()
    assert not reader.is_alive()
    assert reader_state == [([{"id": 2}], None)]
