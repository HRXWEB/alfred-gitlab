import runpy
import sys
from pathlib import Path
from typing import ClassVar

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import update

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
    def __init__(self):
        self.settings = {
            "api_url": "https://gitlab.example.com/api/v4/projects",
            "hosts": [
                {
                    "id": PROFILE_ID,
                    "name": "gitlab.example.com",
                    "api_url": ("https://gitlab.example.com/api/v4/projects"),
                }
            ],
            "default_host_id": PROFILE_ID,
        }

    def get_password(self, name):
        return "example-token"


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
        "current_generation",
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
        "store_projects",
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
