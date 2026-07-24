import runpy
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import update
from host_values import NameSource, ProfileId

PROFILE_ID = "a" * 32


class FakeLogger:
    def debug(self, message):
        pass

    def info(self, message):
        pass


class FakeResponse:
    headers: ClassVar[dict[str, str]] = {}

    def raise_for_status(self):
        pass

    def json(self):
        return []


class FakeWorkflow:
    def __init__(self, data_dir: Path | None = None, args: list[str] | None = None):
        self.data_dir = data_dir
        self.args = args or []
        self.cache = {}
        self.passwords = {
            f"gitlab_api_key:{PROFILE_ID}": "example-token",
        }
        self.settings = {
            "api_url": "https://gitlab.example.com/api/v4/projects",
            "hosts": [
                {
                    "id": PROFILE_ID,
                    "name": "gitlab.example.com",
                    "api_url": ("https://gitlab.example.com/api/v4/projects"),
                    "name_source": "auto",
                }
            ],
            "default_host_id": PROFILE_ID,
        }

    def get_password(self, name):
        return self.passwords[name]

    def datafile(self, name):
        assert self.data_dir is not None
        return str(self.data_dir / name)

    def cache_data(self, name, value):
        if value is None:
            self.cache.pop(name, None)
        else:
            self.cache[name] = value

    def cached_data(self, name, data_func=None, *, max_age=60):
        del data_func, max_age
        return self.cache.get(name)


def test_update_script_propagates_workflow_failure(monkeypatch):
    monkeypatch.setattr(update.Workflow, "run", lambda self, callback: 7)

    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SRC_DIR / "update.py"), run_name="__main__")

    assert error.value.code == 7


def test_generation_is_captured_before_credentials(monkeypatch):
    workflow = FakeWorkflow()
    events = []
    update.log = FakeLogger()
    monkeypatch.setattr(
        update.CacheState,
        "begin_refresh",
        lambda self, profile_id: events.append("generation") or "generation-1",
    )
    monkeypatch.setattr(
        workflow,
        "get_password",
        lambda name: events.append("credentials") or "example-token",
    )
    monkeypatch.setattr(update, "get_projects", lambda key, url: [])
    monkeypatch.setattr(
        update.CacheState,
        "publish_refresh_success",
        lambda self, profile_id, generation, projects: True,
    )

    update.main(workflow)

    assert events == ["generation", "credentials"]


def test_api_token_is_sent_in_header(monkeypatch):
    requests = []
    monkeypatch.setattr(
        update.mureq,
        "get",
        lambda url, **kwargs: requests.append((url, kwargs)) or FakeResponse(),
    )

    update.get_projects(
        "example-token",
        "https://gitlab.example.com/api/v4/projects",
    )

    assert requests[0][1]["headers"] == {"PRIVATE-TOKEN": "example-token"}
    assert "private_token" not in requests[0][1]["params"]


def test_refresh_profile_success_replaces_cache_and_clears_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a profile with old projects and an error status
    workflow = FakeWorkflow(tmp_path)
    cache = update.CacheState(workflow)
    profile = update.HostProfile.from_record(workflow.settings["hosts"][0])
    assert cache.store_projects(profile.id, "", [{"id": 1}])
    cache.store_status(
        profile.id,
        {"ok": False, "category": "HTTPErrorStatus", "http_status": 401},
    )
    expected_projects = [{"id": 2}, {"id": 3}]
    monkeypatch.setattr(
        update,
        "get_projects",
        lambda token, url: expected_projects,
    )

    # When: the profile refresh succeeds
    result = update.refresh_profile(workflow, profile, cache)

    # Then: success replaces that cache and atomically clears its error
    assert result == update.RefreshSuccess(
        host="gitlab.example.com",
        project_count=2,
    )
    assert cache.load_projects(profile.id) == expected_projects
    assert cache.load_status(profile.id) is None


def test_refresh_profile_captures_generation_before_scoped_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: observability at the generation and Keychain boundaries
    workflow = FakeWorkflow(tmp_path)
    cache = update.CacheState(workflow)
    profile = update.HostProfile.from_record(workflow.settings["hosts"][0])
    events = []
    monkeypatch.setattr(
        update.CacheState,
        "begin_refresh",
        lambda self, profile_id: events.append(("generation", profile_id)) or "",
    )
    monkeypatch.setattr(
        update.CacheState,
        "publish_refresh_success",
        lambda self, profile_id, generation, projects: True,
    )
    monkeypatch.setattr(
        workflow,
        "get_password",
        lambda account: events.append(("token", account)) or "example-token",
    )
    monkeypatch.setattr(update, "get_projects", lambda token, url: [])

    # When: the profile refresh reads its inputs
    update.refresh_profile(workflow, profile, cache)

    # Then: generation precedes the correctly scoped Keychain account
    assert events == [
        ("generation", PROFILE_ID),
        ("token", f"gitlab_api_key:{PROFILE_ID}"),
    ]


def test_refresh_profile_failure_keeps_old_cache_and_sanitizes_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a failed remote call whose exception text contains secrets
    class FakeHttpError(update.mureq.HTTPException):
        status_code = 401

    workflow = FakeWorkflow(tmp_path)
    cache = update.CacheState(workflow)
    profile = update.HostProfile.from_record(workflow.settings["hosts"][0])
    old_projects = [{"id": 1}]
    assert cache.store_projects(profile.id, "", old_projects)

    def fail_fetch(token, url):
        raise FakeHttpError(f"{token} {url} secret response body")

    monkeypatch.setattr(update, "get_projects", fail_fetch)

    # When: the profile refresh handles the remote failure
    result = update.refresh_profile(workflow, profile, cache)

    # Then: old projects survive and only allowlisted diagnostics persist
    expected = update.RefreshFailure(
        host="gitlab.example.com",
        category="FakeHttpError",
        http_status=401,
    )
    assert result == expected
    assert cache.load_projects(profile.id) == old_projects
    assert cache.load_status(profile.id) == {
        "ok": False,
        "category": "FakeHttpError",
        "http_status": 401,
    }
    rendered = repr((result, workflow.cache))
    assert "example-token" not in rendered
    assert "https://" not in rendered
    assert "response body" not in rendered


def test_refresh_profiles_bounds_workers_and_preserves_profile_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: six profiles and factories that record each worker-owned facade
    profiles = tuple(
        update.HostProfile(
            id=ProfileId(f"{index:032x}"),
            name=f"host-{index}",
            api_url=f"https://host-{index}.example.test/api/v4/projects",
            name_source=NameSource.AUTO,
        )
        for index in range(6)
    )
    created_workflows = []
    created_caches = []
    active_workers = 0
    peak_workers = 0
    lock = threading.Lock()
    release = threading.Event()

    def workflow_factory():
        workflow = FakeWorkflow(tmp_path)
        created_workflows.append(workflow)
        return workflow

    def cache_factory(workflow):
        cache = update.CacheState(workflow)
        created_caches.append(cache)
        return cache

    def refresh(workflow, profile, cache):
        nonlocal active_workers, peak_workers
        assert cache.workflow is workflow
        with lock:
            active_workers += 1
            peak_workers = max(peak_workers, active_workers)
            if peak_workers == 4:
                release.set()
        assert release.wait(timeout=2)
        with lock:
            active_workers -= 1
        return update.RefreshSuccess(host=profile.name, project_count=1)

    monkeypatch.setattr(update, "refresh_profile", refresh)
    workers = update.RefreshWorkers(workflow_factory, cache_factory)

    # When: all profiles refresh with a requested limit above the hard bound
    results = update.refresh_profiles(profiles, workers, max_workers=20)

    # Then: at most four isolated facades run and input ordering is retained
    assert peak_workers == 4
    assert len({id(workflow) for workflow in created_workflows}) == 6
    assert len({id(cache) for cache in created_caches}) == 6
    assert [result.host for result in results] == [profile.name for profile in profiles]


def test_refresh_profiles_handles_an_empty_registry_without_an_executor() -> None:
    # Given: worker factories that must not be called
    def unexpected_workflow():
        raise AssertionError("empty refresh constructed a workflow")

    def unexpected_cache(workflow):
        raise AssertionError("empty refresh constructed a cache")

    workers = update.RefreshWorkers(unexpected_workflow, unexpected_cache)

    # When: the empty profile registry is refreshed
    results = update.refresh_profiles((), workers)

    # Then: no work is scheduled
    assert results == []


def test_refresh_summary_reports_partial_failure() -> None:
    # Given: two successes and one handled host failure
    results = [
        update.RefreshSuccess(host="company", project_count=2),
        update.RefreshSuccess(host="public", project_count=1),
        update.RefreshFailure(
            host="offline",
            category="HTTPErrorStatus",
            http_status=401,
        ),
    ]

    # When: the coordinator summarizes the outcomes
    summary = update.refresh_summary(results)

    # Then: only aggregate counts are exposed
    assert summary == "2 refreshed, 1 failed"
