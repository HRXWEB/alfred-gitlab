from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import update
from host_values import HostProfile, NameSource, ProfileId
from workflow import PasswordNotFound

PROFILE_ID = "a" * 32


class FakeWorkflow:
    def __init__(self, data_dir: Path, args: list[str] | None = None) -> None:
        self.data_dir = data_dir
        self.args = args or []
        self.cache = {}
        self.password_error: Exception | None = None
        self.settings = {
            "hosts": [
                {
                    "id": PROFILE_ID,
                    "name": "gitlab.example.test",
                    "api_url": "https://gitlab.example.test/api/v4/projects",
                    "name_source": "auto",
                }
            ],
            "default_host_id": PROFILE_ID,
        }

    def get_password(self, name):
        if self.password_error is not None:
            raise self.password_error
        return "example-token"

    def datafile(self, name):
        return str(self.data_dir / name)

    def cache_data(self, name, value):
        if value is None:
            self.cache.pop(name, None)
        else:
            self.cache[name] = value

    def cached_data(self, name, data_func=None, *, max_age=60):
        del data_func, max_age
        return self.cache.get(name)


def profile() -> HostProfile:
    return HostProfile(
        id=ProfileId(PROFILE_ID),
        name="gitlab.example.test",
        api_url="https://gitlab.example.test/api/v4/projects",
        name_source=NameSource.AUTO,
    )


def test_password_not_found_becomes_sanitized_failure(
    tmp_path: Path,
) -> None:
    # Given: a profile whose scoped Keychain token is unavailable
    workflow = FakeWorkflow(tmp_path)
    workflow.password_error = PasswordNotFound()
    cache = update.CacheState(workflow)

    # When: the profile refresh handles the expected credential failure
    result = update.refresh_profile(workflow, profile(), cache)

    # Then: only allowlisted failure metadata is returned and cached
    assert result == update.RefreshFailure(
        host="gitlab.example.test",
        category="PasswordNotFound",
        http_status=None,
    )
    assert cache.load_status(PROFILE_ID) == {
        "ok": False,
        "category": "PasswordNotFound",
        "http_status": None,
    }


def test_http_error_becomes_sanitized_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an expected GitLab HTTP status failure
    workflow = FakeWorkflow(tmp_path)
    cache = update.CacheState(workflow)
    monkeypatch.setattr(
        update,
        "get_projects",
        lambda token, url: (_ for _ in ()).throw(update.mureq.HTTPErrorStatus(503)),
    )

    # When: the profile refresh handles the expected HTTP failure
    result = update.refresh_profile(workflow, profile(), cache)

    # Then: HTTP status is retained without response or request details
    assert result == update.RefreshFailure(
        host="gitlab.example.test",
        category="HTTPErrorStatus",
        http_status=503,
    )
    assert "example-token" not in repr((result, workflow.cache))
    assert "https://" not in repr((result, workflow.cache))


@pytest.mark.parametrize(
    "unexpected",
    [
        RuntimeError("programming error"),
        json.JSONDecodeError("invalid response", "x", 0),
    ],
)
def test_unexpected_fetch_error_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unexpected: Exception,
) -> None:
    # Given: a parser or programming error inside the fetch path
    workflow = FakeWorkflow(tmp_path)
    cache = update.CacheState(workflow)
    monkeypatch.setattr(
        update,
        "get_projects",
        lambda token, url: (_ for _ in ()).throw(unexpected),
    )

    # When / Then: the profile boundary leaves unexpected errors visible
    with pytest.raises(type(unexpected)):
        update.refresh_profile(workflow, profile(), cache)


def test_all_coordinator_propagates_unexpected_refresh_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an all-host refresh whose worker hits a programming error
    root_workflow = FakeWorkflow(tmp_path, ["--all"])
    workers = update.RefreshWorkers(
        workflow_factory=lambda: FakeWorkflow(tmp_path),
        cache_factory=update.CacheState,
    )
    monkeypatch.setattr(update, "_default_workers", lambda: workers)
    monkeypatch.setattr(
        update,
        "get_projects",
        lambda token, url: (_ for _ in ()).throw(RuntimeError("programming error")),
    )

    # When / Then: the future and CLI coordinator propagate the error
    with pytest.raises(RuntimeError, match="programming error"):
        update.main(root_workflow)
