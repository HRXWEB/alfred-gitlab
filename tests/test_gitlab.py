import pickle
import plistlib
import sys
import tempfile
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import cache_state
import gitlab
import hosts
import search_refresh
from host_values import NameSource
from workflow import PasswordNotFound

PROFILE_ID = "a" * 32
PUBLIC_PROFILE_ID = "b" * 32


def load_plist():
    with (SRC_DIR / "info.plist").open("rb") as plist_file:
        return plistlib.load(plist_file)


def test_updates_are_loaded_from_the_fork():
    assert gitlab.UPDATE_REPO == "HRXWEB/alfred-gitlab"


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def info(self, message):
        pass

    def warning(self, message):
        self.warnings.append(message)


class FakeWorkflow:
    def __init__(self, args):
        self.args = args
        self.settings = {
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
        self.saved_passwords = []
        self.passwords = {}
        self.cache_writes = []
        self.cached_values = {}
        self.fresh_cache_keys = set()
        self.freshness_checks = []
        self.items = []
        self.filter_calls = []
        self.feedback_count = 0
        self.rerun = 0.0
        self.update_available = False
        self.data_dir = tempfile.TemporaryDirectory()

    def save_password(self, name, value):
        self.passwords[name] = value
        self.saved_passwords.append((name, value))

    def get_password(self, name):
        try:
            return self.passwords[name]
        except KeyError:
            raise PasswordNotFound() from None

    def delete_password(self, name):
        del self.passwords[name]

    def cache_data(self, name, value):
        self.cache_writes.append((name, value))

    def cached_data(self, name, data_func=None, max_age=60):
        del data_func, max_age
        return self.cached_values.get(name)

    def cached_data_fresh(self, name, max_age):
        self.freshness_checks.append((name, max_age))
        return name in self.fresh_cache_keys

    def add_item(self, title, subtitle=None, **kwargs):
        self.items.append((title, subtitle, kwargs))

    def send_feedback(self):
        self.feedback_count += 1

    def filter(self, query, items, key, min_score):
        self.filter_calls.append((query, min_score))
        return [item for item in items if query in key(item)]

    def workflowfile(self, name):
        return str(SRC_DIR / name)

    def datafile(self, name):
        return str(Path(self.data_dir.name) / name)


class FakeCache:
    def __init__(self, projects=None, statuses=None):
        self.projects = projects or {}
        self.statuses = statuses or {}

    def load_projects(self, profile_id):
        return self.projects.get(profile_id)

    def load_status(self, profile_id):
        return self.statuses.get(profile_id)


def profile(profile_id, name, api_url):
    return {
        "id": profile_id,
        "name": name,
        "api_url": api_url,
        "name_source": NameSource.CUSTOM,
    }


@pytest.mark.parametrize("query", ["my", "my ", "my issues", "my company issues"])
def test_my_query_dispatches_without_project_search(query, monkeypatch):
    workflow = FakeWorkflow([query])
    rendered = []
    monkeypatch.setattr(
        gitlab,
        "render_my_pages",
        lambda *args: rendered.append(args),
        raising=False,
    )
    monkeypatch.setattr(
        gitlab,
        "aggregate_projects",
        lambda *args: pytest.fail("project search ran"),
    )
    gitlab.log = FakeLogger()

    result = gitlab.main(workflow)

    assert result == 0
    assert len(rendered) == 1
    assert rendered[0][0] is workflow
    assert rendered[0][3] == query
    assert workflow.feedback_count == 1


@pytest.mark.parametrize("query", ["myproject", "my-company", "mystery"])
def test_my_prefix_remains_project_search(query, monkeypatch):
    workflow = FakeWorkflow([query])
    aggregate_calls = []
    project = {
        "id": 7,
        "name_with_namespace": "Found / " + query,
        "path_with_namespace": "found/" + query,
        "web_url": "https://gitlab.example.com/found/" + query,
        "_host_name": "gitlab.example.com",
        "_api_url": "https://gitlab.example.com/api/v4/projects",
        "_alfred_uid": PROFILE_ID + ":7",
    }

    def aggregate(workflow_arg, profiles_arg, cache_arg):
        aggregate_calls.append((workflow_arg, profiles_arg, cache_arg))
        return [project]

    monkeypatch.setattr(gitlab, "aggregate_projects", aggregate)
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert len(aggregate_calls) == 1
    assert aggregate_calls[0][0] is workflow
    assert [item["id"] for item in aggregate_calls[0][1]] == [PROFILE_ID]
    assert workflow.filter_calls == [(query, 20)]
    assert [item[0] for item in workflow.items] == ["Found / " + query]


def test_aggregate_projects_preserves_duplicate_project_ids_without_mutation():
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    company_project = {
        "id": 7,
        "name_with_namespace": "Example / Company",
        "path_with_namespace": "example/company",
    }
    public_project = {
        "id": 7,
        "name_with_namespace": "Example / Public",
        "path_with_namespace": "example/public",
    }
    cache = FakeCache(
        projects={
            PROFILE_ID: [company_project],
            PUBLIC_PROFILE_ID: [public_project],
        }
    )

    projects = gitlab.aggregate_projects(
        workflow,
        [company, public],
        cache,
    )

    assert [project["_alfred_uid"] for project in projects] == [
        f"{PROFILE_ID}:7",
        f"{PUBLIC_PROFILE_ID}:7",
    ]
    assert "_host_name" not in company_project
    assert "_host_name" not in public_project


def test_search_key_includes_host_name():
    project = {
        "_host_name": "company",
        "name_with_namespace": "Example / Project",
        "path_with_namespace": "example/project",
    }

    assert "company" in gitlab.search_for_project(project)


def test_one_host_error_does_not_hide_other_projects_or_leak_status_details():
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    cache = FakeCache(
        projects={
            PROFILE_ID: [
                {
                    "id": 7,
                    "name_with_namespace": "Example / Project",
                    "path_with_namespace": "example/project",
                }
            ]
        },
        statuses={
            PUBLIC_PROFILE_ID: {
                "ok": False,
                "category": "placeholder-token https://secret.example/body",
                "http_status": 401,
                "message": "traceback body placeholder-token",
            }
        },
    )

    projects = gitlab.aggregate_projects(
        workflow,
        [company, public],
        cache,
    )

    assert [project["_host_name"] for project in projects] == ["company"]
    assert workflow.items == [
        (
            "public refresh failed",
            "HTTP 401",
            {"valid": False, "icon": gitlab.ICON_WARNING},
        )
    ]


def test_aggregate_projects_refreshes_only_stale_idle_hosts(monkeypatch):
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    workflow.fresh_cache_keys.add(cache_state.projects_key(PROFILE_ID))
    commands = []
    monkeypatch.setattr(
        search_refresh,
        "is_running",
        lambda name: name == f"update-{PROFILE_ID}",
    )
    monkeypatch.setattr(
        search_refresh,
        "run_in_background",
        lambda name, command: commands.append((name, command)),
    )

    gitlab.aggregate_projects(
        workflow,
        [company, public],
        FakeCache(),
    )

    assert workflow.freshness_checks == [
        (cache_state.projects_key(PROFILE_ID), 3600),
        (cache_state.projects_key(PUBLIC_PROFILE_ID), 3600),
    ]
    assert commands == [
        (
            f"update-{PUBLIC_PROFILE_ID}",
            [
                sys.executable,
                str(SRC_DIR / "update.py"),
                "--host-id",
                PUBLIC_PROFILE_ID,
            ],
        )
    ]


def test_aggregate_projects_keeps_retained_data_while_refresh_is_running(
    monkeypatch,
):
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    commands = []
    monkeypatch.setattr(search_refresh, "is_running", lambda _name: True)
    monkeypatch.setattr(
        search_refresh,
        "run_in_background",
        lambda name, command: commands.append((name, command)),
    )

    projects = gitlab.aggregate_projects(
        workflow,
        [company],
        FakeCache(
            projects={
                PROFILE_ID: [
                    {
                        "id": 7,
                        "name_with_namespace": "Retained / Project",
                        "path_with_namespace": "retained/project",
                    }
                ]
            }
        ),
    )

    assert [project["id"] for project in projects] == [7]
    assert commands == []


def test_aggregate_projects_reruns_while_empty_cache_refreshes(monkeypatch):
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    monkeypatch.setattr(search_refresh, "is_running", lambda _name: False)
    monkeypatch.setattr(
        search_refresh,
        "run_in_background",
        lambda _name, _command: None,
    )

    projects = gitlab.aggregate_projects(workflow, [company], FakeCache())

    assert projects == []
    assert workflow.rerun == 0.5


def test_main_renders_each_project_with_its_host_context():
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "http://192.0.2.20/api/v4/projects",
    )
    workflow.settings = {
        "hosts": [company, public],
        "default_host_id": PROFILE_ID,
        "host_schema_version": 1,
    }
    workflow.passwords[hosts.token_account(PROFILE_ID)] = "placeholder-credential"
    workflow.cached_values[cache_state.projects_key(PROFILE_ID)] = [
        {
            "id": 7,
            "name_with_namespace": "Example / Company",
            "path_with_namespace": "example/company",
            "web_url": "http://192.0.2.10/example/company",
        }
    ]
    workflow.cached_values[cache_state.projects_key(PUBLIC_PROFILE_ID)] = [
        {
            "id": 7,
            "name_with_namespace": "Example / Public",
            "path_with_namespace": "example/public",
            "web_url": "http://192.0.2.20/example/public",
        }
    ]
    workflow.fresh_cache_keys.update(
        {
            cache_state.projects_key(PROFILE_ID),
            cache_state.projects_key(PUBLIC_PROFILE_ID),
        }
    )
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.items == [
        (
            "Example / Company",
            "company · example/company",
            {
                "arg": "https://gitlab.company.example/example/company",
                "valid": True,
                "icon": None,
                "uid": f"{PROFILE_ID}:7",
            },
        ),
        (
            "Example / Public",
            "public · example/public",
            {
                "arg": "http://192.0.2.20/example/public",
                "valid": True,
                "icon": None,
                "uid": f"{PUBLIC_PROFILE_ID}:7",
            },
        ),
    ]


def test_main_filters_aggregate_once_using_host_name():
    workflow = FakeWorkflow(["public"])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    workflow.settings = {
        "hosts": [company, public],
        "default_host_id": PROFILE_ID,
        "host_schema_version": 1,
    }
    project = {
        "id": 7,
        "name_with_namespace": "Example / Project",
        "path_with_namespace": "example/project",
        "web_url": "https://gitlab.public.example/example/project",
    }
    workflow.cached_values[cache_state.projects_key(PROFILE_ID)] = [project]
    workflow.cached_values[cache_state.projects_key(PUBLIC_PROFILE_ID)] = [project]
    workflow.fresh_cache_keys.update(
        {
            cache_state.projects_key(PROFILE_ID),
            cache_state.projects_key(PUBLIC_PROFILE_ID),
        }
    )
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.filter_calls == [("public", 20)]
    assert [item[1] for item in workflow.items] == ["public · example/project"]


def test_hostadd_reports_added_then_updated_without_exposing_token(capsys):
    workflow = FakeWorkflow(
        [
            "--hostadd",
            (
                "company https://gitlab.company.example/api/v4/projects "
                "placeholder-credential"
            ),
        ]
    )
    workflow.settings = {}
    logger = FakeLogger()
    gitlab.log = logger

    gitlab.main(workflow)
    first_output = capsys.readouterr().out
    stored = workflow.settings["hosts"][0]
    workflow.args = [
        "--hostadd",
        (
            "company https://gitlab.company.example/api/v4/projects "
            "replacement-credential"
        ),
    ]
    gitlab.main(workflow)
    second_output = capsys.readouterr().out

    assert first_output == "Added company\n"
    assert second_output == "Updated company\n"
    assert "credential" not in repr(workflow.settings)
    assert "credential" not in repr(workflow.cache_writes)
    assert "credential" not in first_output + second_output
    assert logger.warnings == []
    assert workflow.passwords[hosts.token_account(stored["id"])] == (
        "replacement-credential"
    )


def test_hostadd_http_warns_once_without_exposing_token(capsys):
    # Given: a synthetic HTTP host command and an observable workflow logger
    token = "synthetic-http-credential"
    workflow = FakeWorkflow(
        [
            "--hostadd",
            f"lab http://192.0.2.10:8080/api/v4/projects {token}",
        ]
    )
    workflow.settings = {}
    logger = FakeLogger()
    gitlab.log = logger

    # When: the host is added through the primary CLI entrypoint
    gitlab.main(workflow)

    # Then: only the generic warning is logged and stdout remains sanitized
    output = capsys.readouterr().out
    assert logger.warnings == [
        "GitLab API token transport is not encrypted over HTTP"
    ]
    assert output == "Added lab\n"
    assert token not in output
    assert token not in repr(logger.warnings)


def test_render_host_list_shows_counts_and_sanitized_status():
    workflow = FakeWorkflow([])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    cache = FakeCache(
        projects={
            PROFILE_ID: [{"id": 1}, {"id": 2}],
            PUBLIC_PROFILE_ID: [{"id": 3}],
        },
        statuses={
            PUBLIC_PROFILE_ID: {
                "ok": False,
                "category": "placeholder-token https://secret.example/body",
                "http_status": 503,
                "message": "traceback body placeholder-token",
            }
        },
    )

    gitlab.render_host_list(workflow, [company, public], cache)

    assert workflow.items == [
        (
            "company",
            ("https://gitlab.company.example/api/v4/projects · 2 cached · Ready"),
            {"valid": False},
        ),
        (
            "public",
            (
                "https://gitlab.public.example/api/v4/projects · "
                "1 cached · Refresh failed (HTTP 503)"
            ),
            {"valid": False},
        ),
    ]


def test_hostlist_cli_sends_alfred_feedback():
    workflow = FakeWorkflow(["--hostlist"])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    workflow.settings = {
        "hosts": [company],
        "default_host_id": PROFILE_ID,
        "host_schema_version": 1,
    }
    workflow.cached_values[cache_state.projects_key(PROFILE_ID)] = [{"id": 1}]
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.items == [
        (
            "company",
            ("https://gitlab.company.example/api/v4/projects · 1 cached · Ready"),
            {"valid": False},
        )
    ]
    assert workflow.feedback_count == 1


def test_hostdefault_list_renders_feedback():
    workflow = FakeWorkflow(["--hostdefault-list"])
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert workflow.feedback_count == 1
    assert workflow.items[0][1].startswith("Current default · ")


def test_hostdefault_list_without_profiles_shows_setup_guidance():
    workflow = FakeWorkflow(["--hostdefault-list"])
    workflow.settings = {}
    gitlab.log = FakeLogger()

    assert gitlab.main(workflow) == 0
    assert workflow.items == [
        (
            "No API key set.",
            "Please use glsetkey to set your GitLab API key.",
            {"valid": False, "icon": gitlab.ICON_WARNING},
        )
    ]
    assert workflow.feedback_count == 1


def test_hostdefault_action_prints_selected_name(capsys):
    workflow = FakeWorkflow(["--set-default-host", PROFILE_ID])
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert capsys.readouterr().out == "Default host set to gitlab.example.com\n"


def test_hostremove_deletes_exact_profile_token_and_payload_state(capsys):
    workflow = FakeWorkflow(["--hostremove", "company"])
    company = profile(
        PROFILE_ID,
        "company",
        "https://gitlab.company.example/api/v4/projects",
    )
    public = profile(
        PUBLIC_PROFILE_ID,
        "public",
        "https://gitlab.public.example/api/v4/projects",
    )
    workflow.settings = {
        "hosts": [company, public],
        "default_host_id": PROFILE_ID,
        "host_schema_version": 1,
    }
    workflow.passwords[hosts.token_account(PROFILE_ID)] = "placeholder-credential"
    workflow.passwords[hosts.token_account(PUBLIC_PROFILE_ID)] = (
        "public-placeholder-credential"
    )
    gitlab.log = FakeLogger()

    gitlab.main(workflow)

    assert capsys.readouterr().out == "Removed company\n"
    assert [stored["name"] for stored in workflow.settings["hosts"]] == ["public"]
    assert hosts.token_account(PROFILE_ID) not in workflow.passwords
    assert workflow.cache_writes[-2:] == [
        (cache_state.projects_key(PROFILE_ID), None),
        (cache_state.status_key(PROFILE_ID), None),
    ]


def test_refresh_runs_all_hosts_synchronously_and_prints_only_summary(
    monkeypatch,
    capsys,
):
    workflow = FakeWorkflow(["--refresh"])
    commands = []
    gitlab.log = FakeLogger()
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_output",
        lambda command, **kwargs: (
            commands.append((command, kwargs)) or "2 refreshed, 1 failed\n"
        ),
    )

    result = gitlab.main(workflow)

    assert commands == [
        (
            [
                sys.executable,
                str(SRC_DIR / "update.py"),
                "--all",
            ],
            {"universal_newlines": True},
        )
    ]
    assert capsys.readouterr().out == "2 refreshed, 1 failed\n"
    assert result == 0


def test_refresh_migrates_legacy_state_before_running_update(
    monkeypatch,
    capsys,
):
    workflow = FakeWorkflow(["--refresh"])
    workflow.settings = {
        "api_url": "https://gitlab.example.test/api/v4/projects",
    }
    workflow.passwords = {"gitlab_api_key": "example-token"}
    gitlab.log = FakeLogger()
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_output",
        lambda _command, **_kwargs: "1 refreshed, 0 failed\n",
    )

    result = gitlab.main(workflow)

    profiles = hosts.get_profiles(workflow)
    assert result == 0
    assert capsys.readouterr().out == "1 refreshed, 0 failed\n"
    assert len(profiles) == 1
    assert hosts.token_account(profiles[0]["id"]) in workflow.passwords


def test_project_web_url_uses_configured_domain():
    result = gitlab.project_web_url(
        "http://192.0.2.10/teams/sample-project?tab=readme#usage",
        "http://gitlab.example.com/api/v4/projects",
    )

    assert result == ("http://gitlab.example.com/teams/sample-project?tab=readme#usage")


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

    assert workflow.saved_passwords == [(f"gitlab_api_key:{PROFILE_ID}", "new-token")]
    assert workflow.cache_writes == [(cache_state.projects_key(PROFILE_ID), None)]


def test_setting_api_url_invalidates_projects_cache():
    workflow = FakeWorkflow(["--seturl", "http://gitlab.example.com/api/v4/projects"])
    logger = FakeLogger()
    gitlab.log = logger

    gitlab.main(workflow)

    assert workflow.settings["hosts"][0]["api_url"] == (
        "http://gitlab.example.com/api/v4/projects"
    )
    assert workflow.settings["hosts"][0]["name"] == "gitlab.example.com"
    assert "api_url" not in workflow.settings
    assert workflow.cache_writes == [(cache_state.projects_key(PROFILE_ID), None)]
    assert logger.warnings == ["GitLab API token transport is not encrypted over HTTP"]


def test_setting_invalid_api_url_is_rejected():
    workflow = FakeWorkflow(["--seturl", "https://bad_host/api/v4/projects"])
    gitlab.log = FakeLogger()

    with pytest.raises(ValueError):
        gitlab.main(workflow)

    assert "api_url" not in workflow.settings
    assert workflow.cache_writes == []


def test_normal_launch_when_only_legacy_state_exists_migrates_before_auth():
    # Given: a v3.1 workflow with its legacy URL and credential
    workflow = FakeWorkflow([])
    workflow.settings = {"api_url": "https://legacy.example.com/api/v4/projects"}
    workflow.passwords["gitlab_api_key"] = "example-token"
    gitlab.log = FakeLogger()

    # When: the ordinary project search launches
    gitlab.main(workflow)

    # Then: a v4 profile and scoped credential exist while legacy state remains
    profile = hosts.get_default_profile(workflow)
    assert profile is not None
    assert profile["name"] == "legacy.example.com"
    assert workflow.passwords[hosts.token_account(profile["id"])] == ("example-token")
    assert workflow.passwords["gitlab_api_key"] == "example-token"
    assert workflow.settings["api_url"] == (
        "https://legacy.example.com/api/v4/projects"
    )


def test_corrupt_projects_cache_is_invalidated():
    workflow = FakeWorkflow([])
    workflow.cached_data = lambda *args, **kwargs: (_ for _ in ()).throw(
        pickle.UnpicklingError("invalid cache")
    )
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [(cache_state.projects_key(PROFILE_ID), None)]


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
    workflow.cached_data = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [(cache_state.projects_key(PROFILE_ID), None)]


@pytest.mark.parametrize("cached_value", [{"id": 1}, "projects", 7])
def test_invalid_projects_cache_type_is_invalidated(cached_value):
    workflow = FakeWorkflow([])
    workflow.cached_data = lambda *args, **kwargs: cached_value
    gitlab.log = FakeLogger()

    result = gitlab.load_cached_projects(workflow)

    assert result is None
    assert workflow.cache_writes == [(cache_state.projects_key(PROFILE_ID), None)]


def test_refresh_reloads_all_projects_synchronously(monkeypatch, capsys):
    workflow = FakeWorkflow(["--refresh"])
    commands = []
    gitlab.log = FakeLogger()
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_output",
        lambda command, **kwargs: (
            commands.append((command, kwargs)) or "1 refreshed, 0 failed\n"
        ),
    )

    result = gitlab.main(workflow)

    assert workflow.cache_writes == []
    assert commands == [
        (
            [sys.executable, str(SRC_DIR / "update.py"), "--all"],
            {"universal_newlines": True},
        )
    ]
    assert capsys.readouterr().out == "1 refreshed, 0 failed\n"
    assert result == 0


def test_refresh_propagates_update_failure(monkeypatch):
    workflow = FakeWorkflow(["--refresh"])
    gitlab.log = FakeLogger()
    failure = lambda command, **kwargs: (_ for _ in ()).throw(
        gitlab.subprocess.CalledProcessError(1, command)
    )
    monkeypatch.setattr(
        gitlab.subprocess,
        "check_output",
        failure,
    )

    try:
        gitlab.main(workflow)
    except gitlab.subprocess.CalledProcessError as error:
        assert error.returncode == 1
    else:
        raise AssertionError("refresh failure was not propagated")


def test_workflow_exposes_glrefresh_keyword():
    workflow = load_plist()

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


def test_v4_workflow_metadata():
    workflow = load_plist()

    assert workflow["version"] == "4.1.0"
    assert workflow["createdby"] == "HRXWEB"
    assert workflow["bundleid"] == "com.lukewaite.alfred-gitlab"


@pytest.mark.parametrize(
    ("keyword", "script"),
    [
        ("glhostadd", 'python3 gitlab.py --hostadd "{query}"'),
        ("glhostremove", 'python3 gitlab.py --hostremove "{query}"'),
    ],
)
def test_host_keyword_connection(keyword, script):
    workflow = load_plist()
    keyword_object = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == keyword
    )
    destination_uid = workflow["connections"][keyword_object["uid"]][0][
        "destinationuid"
    ]
    action = next(
        item for item in workflow["objects"] if item["uid"] == destination_uid
    )

    assert action["config"]["script"] == script


@pytest.mark.parametrize("keyword", ["glhostadd", "glhostremove"])
def test_host_mutation_keyword_requires_argument_and_notifies_with_output(keyword):
    workflow = load_plist()
    objects_by_uid = {item["uid"]: item for item in workflow["objects"]}
    keyword_object = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == keyword
    )
    action_uid = workflow["connections"][keyword_object["uid"]][0]["destinationuid"]
    notification_uid = workflow["connections"][action_uid][0]["destinationuid"]

    assert keyword_object["type"] == "alfred.workflow.input.keyword"
    assert keyword_object["config"]["argumenttype"] == 0
    assert keyword_object["config"]["withspace"] is True
    assert objects_by_uid[notification_uid]["config"]["text"] == "{query}"


def test_hostlist_is_no_argument_script_filter():
    workflow = load_plist()
    hostlist = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == "glhostlist"
    )

    assert hostlist["type"] == "alfred.workflow.input.scriptfilter"
    assert hostlist["config"]["argumenttype"] == 2
    assert hostlist["config"]["withspace"] is False
    assert hostlist["config"]["script"] == "python3 gitlab.py --hostlist"


def test_hostdefault_workflow_connection():
    workflow = load_plist()
    objects_by_uid = {item["uid"]: item for item in workflow["objects"]}
    chooser = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == "glhostdefault"
    )

    assert chooser["type"] == "alfred.workflow.input.scriptfilter"
    assert chooser["config"]["argumenttype"] == 2
    assert chooser["config"]["withspace"] is False
    assert chooser["config"]["script"] == "python3 gitlab.py --hostdefault-list"
    action_uid = workflow["connections"][chooser["uid"]][0]["destinationuid"]
    action = objects_by_uid[action_uid]
    assert action["type"] == "alfred.workflow.action.script"
    assert action["config"]["script"] == (
        'python3 gitlab.py --set-default-host "{query}"'
    )

    notification_uid = workflow["connections"][action_uid][0]["destinationuid"]
    notification = objects_by_uid[notification_uid]
    assert notification["type"] == "alfred.workflow.output.notification"
    assert notification["config"]["title"] == "Default GitLab Host Changed"
    assert notification["config"]["text"] == "{query}"


def test_refresh_notification_uses_summary_output():
    workflow = load_plist()
    refresh = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == "glrefresh"
    )
    script_uid = workflow["connections"][refresh["uid"]][0]["destinationuid"]
    notification_uid = workflow["connections"][script_uid][0]["destinationuid"]
    notification = next(
        item for item in workflow["objects"] if item["uid"] == notification_uid
    )

    assert notification["config"]["text"] == "{query}"


def test_workflow_graph_uids_and_connections_are_valid():
    workflow = load_plist()
    object_uids = [item["uid"] for item in workflow["objects"]]

    assert len(object_uids) == len(set(object_uids))
    assert set(workflow["connections"]).issubset(object_uids)
    assert {
        connection["destinationuid"]
        for connections in workflow["connections"].values()
        for connection in connections
    }.issubset(object_uids)


def test_workflow_uses_gitlab_16_subpage_paths():
    workflow = load_plist()

    list_items = [
        item["config"]["items"]
        for item in workflow["objects"]
        if item["type"] == "alfred.workflow.input.listfilter"
    ]

    assert len(list_items) == 1
    assert '"arg":"-/pipelines"' in list_items[0]
    assert '"arg":"-/issues"' in list_items[0]
    assert '"arg":"-/merge_requests"' in list_items[0]
    assert '"arg":"-/project_members"' in list_items[0]
    assert '"arg":"-/network/main"' in list_items[0]
    assert '"arg":"-/settings/ci_cd"' in list_items[0]


def test_workflow_exposes_quick_open_configuration():
    workflow = load_plist()

    quick_open = next(
        item
        for item in workflow["userconfigurationconfig"]
        if item["variable"] == "quick_open"
    )

    assert workflow["variables"]["quick_open"] == "0"
    assert quick_open["type"] == "checkbox"
    assert quick_open["config"]["default"] is False
