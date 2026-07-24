import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import update
from host_values import NameSource, ProfileId

PROFILE_ID = "a" * 32


class FakeWorkflow:
    def __init__(self, data_dir: Path, args: list[str]) -> None:
        self.data_dir = data_dir
        self.args = args
        self.cache = {}
        self.passwords = {
            f"gitlab_api_key:{PROFILE_ID}": "example-token",
        }
        self.settings = {
            "hosts": [
                {
                    "id": PROFILE_ID,
                    "name": "gitlab.example.com",
                    "api_url": "https://gitlab.example.com/api/v4/projects",
                    "name_source": "auto",
                }
            ],
            "default_host_id": PROFILE_ID,
        }

    def get_password(self, name):
        return self.passwords[name]

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


def test_host_id_refreshes_exact_profile_and_exits_nonzero_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a single-host CLI refresh whose fetch is handled as a failure
    workflow = FakeWorkflow(tmp_path, ["--host-id", PROFILE_ID])
    refreshed = []

    def fail_refresh(worker_workflow, profile, cache):
        refreshed.append((worker_workflow, profile.id, cache.workflow))
        return update.RefreshFailure(
            host=profile.name,
            category="HTTPErrorStatus",
            http_status=401,
        )

    monkeypatch.setattr(update, "refresh_profile", fail_refresh)

    # When: the selected profile is refreshed
    exit_code = update.main(workflow)

    # Then: exactly that profile runs and its handled failure is nonzero
    assert exit_code == 1
    assert refreshed == [(workflow, PROFILE_ID, workflow)]


def test_all_refresh_prints_only_summary_and_accepts_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an all-host CLI refresh with one success and one handled failure
    workflow = FakeWorkflow(tmp_path, ["--all"])
    second_id = "b" * 32
    workflow.settings["hosts"].append(
        {
            "id": second_id,
            "name": "offline.example.test",
            "api_url": "https://offline.example.test/api/v4/projects",
            "name_source": "auto",
        }
    )
    seen_profiles = []

    def partial_refresh(profiles, workers, max_workers=4):
        del workers, max_workers
        seen_profiles.extend(profile.id for profile in profiles)
        return [
            update.RefreshSuccess(
                host="gitlab.example.com",
                project_count=2,
            ),
            update.RefreshFailure(
                host="offline.example.test",
                category="HTTPErrorStatus",
                http_status=503,
            ),
        ]

    monkeypatch.setattr(update, "refresh_profiles", partial_refresh)

    # When: all configured profiles are refreshed
    exit_code = update.main(workflow)

    # Then: partial failure is successful at the coordinator boundary
    assert exit_code == 0
    assert seen_profiles == [PROFILE_ID, second_id]
    assert capsys.readouterr().out == "1 refreshed, 1 failed\n"


def test_refresh_profiles_propagates_unexpected_worker_failure(
    tmp_path: Path,
) -> None:
    # Given: one worker whose cache construction fails unexpectedly
    class CoordinatorFailure(Exception):
        pass

    profile = update.HostProfile(
        id=ProfileId(PROFILE_ID),
        name="gitlab.example.com",
        api_url="https://gitlab.example.com/api/v4/projects",
        name_source=NameSource.AUTO,
    )

    def workflow_factory():
        return FakeWorkflow(tmp_path, [])

    def broken_cache(workflow):
        raise CoordinatorFailure

    workers = update.RefreshWorkers(workflow_factory, broken_cache)

    # When / Then: the coordinator does not translate its own failure
    with pytest.raises(CoordinatorFailure):
        update.refresh_profiles((profile,), workers)
