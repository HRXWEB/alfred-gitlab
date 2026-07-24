from pathlib import Path
import plistlib
import sys
import tempfile

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import gitlab


def test_updates_are_loaded_from_the_fork():
    assert gitlab.UPDATE_REPO == "HRXWEB/alfred-gitlab"


class FakeLogger:
    def info(self, message):
        pass

    def warning(self, message):
        pass


class FakeWorkflow:
    def __init__(self, args):
        self.args = args
        self.settings = {}
        self.saved_passwords = []
        self.cache_writes = []
        self.data_dir = tempfile.TemporaryDirectory()

    def save_password(self, name, value):
        self.saved_passwords.append((name, value))

    def cache_data(self, name, value):
        self.cache_writes.append((name, value))

    def workflowfile(self, name):
        return str(SRC_DIR / name)

    def datafile(self, name):
        return str(Path(self.data_dir.name) / name)


def test_project_web_url_uses_configured_domain():
    result = gitlab.project_web_url(
        "http://192.0.2.10/teams/sample-project?tab=readme#usage",
        "http://gitlab.example.com/api/v4/projects",
    )

    assert result == (
        "http://gitlab.example.com/"
        "teams/sample-project?tab=readme#usage"
    )


def test_project_web_url_preserves_url_for_configured_ipv4():
    project_url = "http://192.0.2.10/teams/sample-project"

    result = gitlab.project_web_url(
        project_url,
        "http://192.0.2.10/api/v4/projects",
    )

    assert result == project_url


def test_project_web_url_preserves_url_for_configured_ipv6():
    project_url = "http://[2001:db8::10]/teams/sample-project"

    result = gitlab.project_web_url(
        project_url,
        "http://[2001:db8::10]/api/v4/projects",
    )

    assert result == project_url


def test_project_web_url_preserves_url_without_configured_host():
    project_url = "https://canonical.example/teams/sample-project"

    result = gitlab.project_web_url(
        project_url,
        "gitlab.example.com/api/v4/projects",
    )

    assert result == project_url


def test_project_web_url_preserves_url_with_configured_credentials():
    project_url = "https://canonical.example/teams/sample-project"

    result = gitlab.project_web_url(
        project_url,
        "https://user:password@gitlab.example.com/api/v4/projects",
    )

    assert result == project_url


def test_project_web_url_preserves_url_for_invalid_domain():
    project_url = "https://canonical.example/teams/sample-project"

    for api_url in (
        "https://bad..example/api/v4/projects",
        "https://bad_host/api/v4/projects",
        "https://-bad.example/api/v4/projects",
        "http://[::1/api/v4/projects",
    ):
        assert gitlab.project_web_url(project_url, api_url) == project_url


def test_setting_api_key_invalidates_projects_cache():
    workflow = FakeWorkflow(["--setkey", "new-token"])
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.saved_passwords == [("gitlab_api_key", "new-token")]
    assert workflow.cache_writes == [("projects", None)]


def test_setting_api_url_invalidates_projects_cache():
    workflow = FakeWorkflow(
        ["--seturl", "http://gitlab.example.com/api/v4/projects"]
    )
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.settings == {
        "api_url": "http://gitlab.example.com/api/v4/projects"
    }
    assert workflow.cache_writes == [("projects", None)]


def test_setting_invalid_api_url_is_rejected():
    workflow = FakeWorkflow(["--seturl", "https://bad_host/api/v4/projects"])
    gitlab.log = FakeLogger()

    with pytest.raises(ValueError):
        gitlab.main(workflow)

    assert workflow.settings == {}
    assert workflow.cache_writes == []


def test_corrupt_projects_cache_is_invalidated():
    workflow = FakeWorkflow([])
    workflow.cached_data = lambda *args, **kwargs: (_ for _ in ()).throw(
        gitlab.pickle.UnpicklingError("invalid cache")
    )
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [("projects", None)]


@pytest.mark.parametrize(
    "error",
    [
        ModuleNotFoundError("removed module"),
        ValueError("unsupported pickle protocol"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_stale_pickle_dependency_is_invalidated(error):
    workflow = FakeWorkflow([])
    workflow.cached_data = lambda *args, **kwargs: (_ for _ in ()).throw(
        error
    )
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [("projects", None)]


@pytest.mark.parametrize("cached_value", [{"id": 1}, "projects", 7])
def test_invalid_projects_cache_type_is_invalidated(cached_value):
    workflow = FakeWorkflow([])
    workflow.cached_data = lambda *args, **kwargs: cached_value
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [("projects", None)]


def test_refresh_reloads_projects_synchronously(monkeypatch):
    workflow = FakeWorkflow(["--refresh"])
    commands = []
    gitlab.log = FakeLogger()
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_call",
        lambda command: commands.append(command) or 0,
    )

    result = gitlab.main(workflow)

    assert workflow.cache_writes == [("projects", None)]
    assert commands == [[sys.executable, str(SRC_DIR / "update.py")]]
    assert result == 0


def test_refresh_propagates_update_failure(monkeypatch):
    workflow = FakeWorkflow(["--refresh"])
    gitlab.log = FakeLogger()
    failure = lambda command: (_ for _ in ()).throw(
        gitlab.subprocess.CalledProcessError(1, command)
    )
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_call",
        failure,
    )
    monkeypatch.setattr(gitlab.subprocess, "call", failure)

    try:
        gitlab.main(workflow)
    except gitlab.subprocess.CalledProcessError as error:
        assert error.returncode == 1
    else:
        raise AssertionError("refresh failure was not propagated")


def test_workflow_exposes_glrefresh_keyword():
    with (SRC_DIR / "info.plist").open("rb") as plist_file:
        workflow = plistlib.load(plist_file)

    objects = workflow["objects"]
    refresh_keywords = [
        item
        for item in objects
        if item["type"] == "alfred.workflow.input.keyword"
        and item["config"]["keyword"] == "glrefresh"
    ]

    assert len(refresh_keywords) == 1
    refresh_keyword = refresh_keywords[0]
    assert refresh_keyword["config"]["argumenttype"] == 2
    assert refresh_keyword["config"]["withspace"] is False
    keyword_uid = refresh_keyword["uid"]
    script_uid = workflow["connections"][keyword_uid][0]["destinationuid"]
    script = next(item for item in objects if item["uid"] == script_uid)
    assert script["config"]["script"] == "python3 gitlab.py --refresh"


def test_workflow_uses_gitlab_16_subpage_paths():
    with (SRC_DIR / "info.plist").open("rb") as plist_file:
        workflow = plistlib.load(plist_file)

    list_items = [
        item["config"]["items"]
        for item in workflow["objects"]
        if item["type"] == "alfred.workflow.input.listfilter"
    ]

    assert len(list_items) == 1
    assert '"arg":"-/pipelines"' in list_items[0]
    assert '"arg":"-/issues"' in list_items[0]
    assert '"arg":"-/merge_requests"' in list_items[0]
