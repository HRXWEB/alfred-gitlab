import pickle
import plistlib
import re
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import mureq
import personal_pages
from host_values import NameSource
from workflow import KeychainError, PasswordNotFound


PROFILE_ID = "a" * 32
COMPANY_PROFILE_ID = "b" * 32


class FakeItem:
    def __init__(self, variables):
        self.variables = variables

    def setvar(self, name, value):
        self.variables[name] = value


class FakeWorkflow:
    def __init__(self):
        self.passwords = {}
        self.cached_values = {}
        self.cache_reads = []
        self.cache_writes = []
        self.items = []
        self.item_variables = []
        self.filter_calls = []

    def get_password(self, account):
        try:
            return self.passwords[account]
        except KeyError:
            raise PasswordNotFound() from None

    def cached_data(self, name, data_func=None, *, max_age=60):
        del data_func
        self.cache_reads.append((name, max_age))
        return self.cached_values.get(name)

    def cache_data(self, name, value):
        self.cache_writes.append((name, value))
        self.cached_values[name] = value

    def add_item(self, title, subtitle=None, **kwargs):
        self.items.append((title, subtitle, kwargs))
        variables = {}
        self.item_variables.append(variables)
        return FakeItem(variables)

    def filter(self, query, items, key, min_score):
        self.filter_calls.append((query, min_score))
        lowered = query.lower()
        return [item for item in items if lowered in key(item).lower()]


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def profile():
    return {
        "id": PROFILE_ID,
        "name": "gitlab.example",
        "api_url": "https://gitlab.example/api/v4/projects",
        "name_source": NameSource.CUSTOM,
    }


def named_profile(profile_id, name, api_url):
    return {
        "id": profile_id,
        "name": name,
        "api_url": api_url,
        "name_source": NameSource.CUSTOM,
    }


def configured_profiles():
    first = named_profile(
        PROFILE_ID,
        "first",
        "https://first.example/api/v4/projects",
    )
    company = named_profile(
        COMPANY_PROFILE_ID,
        "company",
        "https://company.example/api/v4/projects",
    )
    return first, company


def cache_username(workflow, profile_id, username):
    workflow.cached_values[personal_pages.identity_cache_key(profile_id)] = username


def _expand_graph_template(template, query, variables):
    expanded = template.replace("{query}", query)
    return re.sub(
        r"\{var:([^}]+)\}",
        lambda match: variables.get(match.group(1), ""),
        expanded,
    )


def _apply_argument_utility(argument, query, variables):
    current = dict(variables)
    variables.update(
        {
            name: _expand_graph_template(value, query, current)
            for name, value in argument["config"]["variables"].items()
        }
    )


def _resolve_quick_open_graph(query, item_variables):
    with (SRC_DIR / "info.plist").open("rb") as plist_file:
        workflow = plistlib.load(plist_file)
    objects = {item["uid"]: item for item in workflow["objects"]}
    script_filter = next(
        item
        for item in workflow["objects"]
        if item.get("config", {}).get("keyword") == "gl"
    )
    branch_filters = [
        objects[edge["destinationuid"]]
        for edge in workflow["connections"][script_filter["uid"]]
    ]
    quick_filter = next(
        item
        for item in branch_filters
        if item["config"]["matchmode"] == 0
        and item["config"]["matchstring"] == "1"
    )
    argument = objects[
        workflow["connections"][quick_filter["uid"]][0]["destinationuid"]
    ]
    open_url = objects[
        workflow["connections"][argument["uid"]][0]["destinationuid"]
    ]
    variables = dict(workflow["variables"])
    variables.update(item_variables)
    _apply_argument_utility(argument, query, variables)
    return _expand_graph_template(open_url["config"]["url"], query, variables)


def _resolve_project_subpage_graph(repository_url, subpage):
    with (SRC_DIR / "info.plist").open("rb") as plist_file:
        workflow = plistlib.load(plist_file)
    objects = {item["uid"]: item for item in workflow["objects"]}
    list_filter = next(
        item
        for item in workflow["objects"]
        if item["type"] == "alfred.workflow.input.listfilter"
    )
    branches = [
        objects[connection["destinationuid"]]
        for connection in workflow["connections"][list_filter["uid"]]
    ]
    ssh_open_branch = next(
        item
        for item in branches
        if item["type"] == "alfred.workflow.utility.filter"
        and item["config"]["matchmode"] == 1
        and item["config"]["matchstring"] == "copy-ssh"
    )
    http_open_branch = objects[
        workflow["connections"][ssh_open_branch["uid"]][0]["destinationuid"]
    ]
    assert http_open_branch["config"]["matchmode"] == 1
    assert http_open_branch["config"]["matchstring"] == "copy-http"
    subpage_argument = objects[
        workflow["connections"][http_open_branch["uid"]][0]["destinationuid"]
    ]
    open_url = objects[
        workflow["connections"][subpage_argument["uid"]][0]["destinationuid"]
    ]
    repository_argument = next(
        item
        for item in workflow["objects"]
        if item["type"] == "alfred.workflow.utility.argument"
        and item["config"].get("variables") == {"repo": "{query}"}
    )
    variables = dict(workflow["variables"])
    _apply_argument_utility(repository_argument, repository_url, variables)
    _apply_argument_utility(subpage_argument, subpage, variables)
    return _expand_graph_template(open_url["config"]["url"], subpage, variables)


@pytest.mark.parametrize(
    ("api_url", "web_root", "api_root"),
    [
        (
            "https://gitlab.com/api/v4/projects",
            "https://gitlab.com",
            "https://gitlab.com/api/v4",
        ),
        (
            "https://gitlab.example:8443/gitlab/api/v4/projects",
            "https://gitlab.example:8443/gitlab",
            "https://gitlab.example:8443/gitlab/api/v4",
        ),
        (
            "http://192.0.2.10:8080/api/v4/projects",
            "http://192.0.2.10:8080",
            "http://192.0.2.10:8080/api/v4",
        ),
        (
            "http://[2001:db8::10]:8080/api/v4/projects",
            "http://[2001:db8::10]:8080",
            "http://[2001:db8::10]:8080/api/v4",
        ),
    ],
)
def test_derive_gitlab_roots(api_url, web_root, api_root):
    assert personal_pages.derive_gitlab_roots(api_url) == (
        personal_pages.GitLabRoots(web_root=web_root, api_root=api_root)
    )


def test_derive_gitlab_roots_rejects_unexpected_api_path():
    assert personal_pages.derive_gitlab_roots(
        "https://gitlab.example/api/v4/groups"
    ) is None


@pytest.mark.parametrize(
    "api_url",
    [
        "https://user:secret@example.invalid/api/v4/projects",
        "ftp://example.invalid/api/v4/projects",
    ],
)
def test_derive_gitlab_roots_rejects_unsafe_api_url(api_url):
    assert personal_pages.derive_gitlab_roots(api_url) is None


def test_personal_pages_follow_gitlab_navigation_order():
    assert personal_pages.PERSONAL_PAGES == (
        personal_pages.PersonalPage(
            "profile", "Profile", "View your public user profile", "/{username}", True
        ),
        personal_pages.PersonalPage(
            "starred projects", "Starred projects", "View your starred projects",
            "/dashboard/projects/starred",
        ),
        personal_pages.PersonalPage(
            "snippets", "Snippets", "View your snippets", "/dashboard/snippets"
        ),
        personal_pages.PersonalPage(
            "merge requests", "Merge requests", "View your merge requests",
            "/dashboard/merge_requests",
        ),
        personal_pages.PersonalPage(
            "projects", "Projects", "View your projects", "/dashboard/projects"
        ),
        personal_pages.PersonalPage(
            "issues", "Issues", "View your issues", "/dashboard/issues"
        ),
        personal_pages.PersonalPage(
            "preferences", "Preferences", "Edit your preferences",
            "/-/profile/preferences",
        ),
        personal_pages.PersonalPage(
            "dashboard", "Dashboard", "View your dashboard", "/"
        ),
        personal_pages.PersonalPage(
            "to-do list", "To-do list", "View your to-do list", "/dashboard/todos"
        ),
    )


def test_identity_cache_key_is_profile_scoped():
    assert personal_pages.identity_cache_key(PROFILE_ID) == (
        "gitlab-username-" + PROFILE_ID
    )


def test_resolve_username_fetches_and_caches_identity(monkeypatch):
    workflow = FakeWorkflow()
    workflow.passwords["gitlab_api_key:" + PROFILE_ID] = "secret-token"
    roots = personal_pages.GitLabRoots(
        web_root="https://gitlab.example",
        api_root="https://gitlab.example/api/v4",
    )
    calls = []
    monkeypatch.setattr(personal_pages, "mureq", mureq, raising=False)
    monkeypatch.setattr(
        mureq,
        "get",
        lambda url, headers: calls.append((url, headers))
        or FakeResponse({"username": "alice"}),
    )

    assert personal_pages.resolve_username(workflow, profile(), roots) == "alice"
    assert calls == [
        (
            "https://gitlab.example/api/v4/user",
            {"PRIVATE-TOKEN": "secret-token"},
        )
    ]
    assert workflow.cache_writes == [("gitlab-username-" + PROFILE_ID, "alice")]
    assert personal_pages.resolve_username(workflow, profile(), roots) == "alice"
    assert len(calls) == 1


def test_resolve_username_uses_non_empty_cached_value_without_keychain():
    workflow = FakeWorkflow()
    workflow.cached_values["gitlab-username-" + PROFILE_ID] = "alice"
    roots = personal_pages.GitLabRoots(
        web_root="https://gitlab.example",
        api_root="https://gitlab.example/api/v4",
    )

    assert personal_pages.resolve_username(workflow, profile(), roots) == "alice"
    assert workflow.cache_reads == [
        ("gitlab-username-" + PROFILE_ID, personal_pages.IDENTITY_CACHE_AGE)
    ]
    assert workflow.cache_writes == []


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(error=mureq.HTTPException("HTTP secret")),
        FakeResponse(ValueError("malformed secret response")),
        FakeResponse({}),
        FakeResponse({"username": ""}),
    ],
    ids=["http_failure", "malformed_json", "missing_username", "empty_username"],
)
def test_resolve_username_sanitizes_response_failures(response, monkeypatch, capsys):
    workflow = FakeWorkflow()
    workflow.passwords["gitlab_api_key:" + PROFILE_ID] = "secret-token"
    roots = personal_pages.GitLabRoots(
        web_root="https://gitlab.example",
        api_root="https://gitlab.example/api/v4",
    )
    monkeypatch.setattr(personal_pages, "mureq", mureq, raising=False)
    monkeypatch.setattr(mureq, "get", lambda *args, **kwargs: response)

    assert personal_pages.resolve_username(workflow, profile(), roots) is None
    assert workflow.cache_writes == []
    assert workflow.items == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_resolve_username_sanitizes_missing_token(capsys):
    workflow = FakeWorkflow()
    roots = personal_pages.GitLabRoots(
        web_root="https://gitlab.example",
        api_root="https://gitlab.example/api/v4",
    )

    assert personal_pages.resolve_username(workflow, profile(), roots) is None
    assert workflow.cache_writes == []
    assert workflow.items == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_render_my_pages_keeps_other_pages_when_keychain_access_fails(capsys):
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    workflow.get_password = lambda _account: (_ for _ in ()).throw(
        KeychainError("credential detail")
    )

    personal_pages.render_my_pages(workflow, [first], first, "my")

    assert workflow.items[0] == (
        "my profile",
        "first · Profile unavailable; try again later",
        {"valid": False},
    )
    assert len(workflow.items) == 9
    assert all(item[2]["valid"] is True for item in workflow.items[1:])
    assert "credential detail" not in repr(workflow.items)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "cache_error",
    [
        pickle.UnpicklingError("corrupt identity cache"),
        EOFError("truncated identity cache"),
    ],
    ids=["unpickling_error", "eof_error"],
)
def test_render_my_pages_discards_corrupt_identity_cache_and_refreshes_username(
    cache_error,
    monkeypatch,
):
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    workflow.passwords["gitlab_api_key:" + PROFILE_ID] = "secret-token"
    workflow.cached_data = lambda *args, **kwargs: (_ for _ in ()).throw(
        cache_error
    )
    monkeypatch.setattr(
        mureq,
        "get",
        lambda *args, **kwargs: FakeResponse({"username": "alice"}),
    )

    personal_pages.render_my_pages(workflow, [first], first, "my")

    assert workflow.items[0][2] == {
        "arg": "https://first.example/alice",
        "valid": True,
    }
    assert len(workflow.items) == 9
    assert all(item[2]["valid"] is True for item in workflow.items)
    assert workflow.cache_writes == [
        (personal_pages.identity_cache_key(PROFILE_ID), None),
        (personal_pages.identity_cache_key(PROFILE_ID), "alice"),
    ]


def test_render_my_pages_uses_fetched_username_when_identity_cache_write_fails(
    monkeypatch,
):
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    workflow.passwords["gitlab_api_key:" + PROFILE_ID] = "secret-token"
    workflow.cache_data = lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("identity cache unavailable")
    )
    monkeypatch.setattr(
        mureq,
        "get",
        lambda *args, **kwargs: FakeResponse({"username": "alice"}),
    )

    personal_pages.render_my_pages(workflow, [first], first, "my")

    assert workflow.items[0][2] == {
        "arg": "https://first.example/alice",
        "valid": True,
    }
    assert len(workflow.items) == 9
    assert all(item[2]["valid"] is True for item in workflow.items)


def test_render_my_pages_lists_default_pages_before_other_hosts():
    workflow = FakeWorkflow()
    first, company = configured_profiles()
    cache_username(workflow, PROFILE_ID, "alice")

    personal_pages.render_my_pages(workflow, [first, company], first, "my")

    assert len(workflow.items) == 10
    assert workflow.items[0][0:2] == (
        "my profile",
        "first · View your public user profile",
    )
    assert workflow.items[0][2]["arg"] == "https://first.example/alice"
    assert workflow.items[0][2]["valid"] is True
    assert workflow.item_variables[0] == {
        "quick_open": "1",
        "url_separator": "",
    }
    assert workflow.items[9] == (
        "my company",
        "Browse personal pages on company",
        {"autocomplete": "my company ", "valid": False},
    )
    assert workflow.item_variables[9] == {}


def test_render_my_pages_selects_exact_host_without_navigation_rows():
    workflow = FakeWorkflow()
    first, company = configured_profiles()
    cache_username(workflow, COMPANY_PROFILE_ID, "bob")

    personal_pages.render_my_pages(
        workflow,
        [first, company],
        first,
        "my company ",
    )

    assert len(workflow.items) == 9
    assert workflow.items[0][0:2] == (
        "my profile",
        "company · View your public user profile",
    )
    assert workflow.items[0][2] == {
        "arg": "https://company.example/bob",
        "valid": True,
    }
    assert all(
        variables == {"quick_open": "1", "url_separator": ""}
        for variables in workflow.item_variables
    )


def test_render_my_pages_filters_selected_host_pages():
    workflow = FakeWorkflow()
    first, company = configured_profiles()

    personal_pages.render_my_pages(
        workflow,
        [first, company],
        first,
        "my company issues",
    )

    assert workflow.items == [
        (
            "my issues",
            "company · View your issues",
            {"arg": "https://company.example/dashboard/issues", "valid": True},
        )
    ]
    assert workflow.item_variables == [
        {"quick_open": "1", "url_separator": ""}
    ]


def test_render_my_pages_filters_multi_word_default_page():
    workflow = FakeWorkflow()
    first, company = configured_profiles()

    personal_pages.render_my_pages(
        workflow,
        [first, company],
        first,
        "my merge requests",
    )

    assert workflow.items == [
        (
            "my merge requests",
            "first · View your merge requests",
            {"arg": "https://first.example/dashboard/merge_requests", "valid": True},
        )
    ]


@pytest.mark.parametrize(
    ("query", "title", "subtitle", "destination"),
    [
        (
            "my issues ",
            "my issues",
            "first · View your issues",
            "https://first.example/dashboard/issues",
        ),
        (
            "my merge requests ",
            "my merge requests",
            "first · View your merge requests",
            "https://first.example/dashboard/merge_requests",
        ),
    ],
)
def test_render_my_pages_trailing_page_filter_does_not_warn(
    query,
    title,
    subtitle,
    destination,
):
    workflow = FakeWorkflow()
    first, company = configured_profiles()

    personal_pages.render_my_pages(workflow, [first, company], first, query)

    assert workflow.items == [
        (title, subtitle, {"arg": destination, "valid": True})
    ]


def test_render_my_pages_trailing_host_prefix_remains_navigation_filter():
    workflow = FakeWorkflow()
    first, company = configured_profiles()

    personal_pages.render_my_pages(workflow, [first, company], first, "my comp ")

    assert workflow.items == [
        (
            "my company",
            "Browse personal pages on company",
            {"autocomplete": "my company ", "valid": False},
        )
    ]


def test_render_my_pages_warns_when_removed_host_query_has_page_filter():
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    cache_username(workflow, PROFILE_ID, "alice")

    personal_pages.render_my_pages(workflow, [first], first, "my removed issues")

    assert workflow.items[0] == (
        "GitLab host is no longer configured",
        None,
        {"valid": False},
    )
    assert [item[0] for item in workflow.items[1:]] == [
        "my profile",
        "my starred projects",
        "my snippets",
        "my merge requests",
        "my projects",
        "my issues",
        "my preferences",
        "my dashboard",
        "my to-do list",
    ]


def test_render_my_pages_filters_non_default_host_navigation():
    workflow = FakeWorkflow()
    first, company = configured_profiles()

    personal_pages.render_my_pages(workflow, [first, company], first, "my comp")

    assert workflow.items == [
        (
            "my company",
            "Browse personal pages on company",
            {"autocomplete": "my company ", "valid": False},
        )
    ]


def test_render_my_pages_omits_host_navigation_for_single_host():
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    cache_username(workflow, PROFILE_ID, "alice")

    personal_pages.render_my_pages(workflow, [first], first, "my")

    assert len(workflow.items) == 9
    assert all(item[0].startswith("my ") for item in workflow.items)
    assert all("autocomplete" not in item[2] for item in workflow.items)


def test_render_my_pages_preserves_installation_subpath_for_every_destination():
    workflow = FakeWorkflow()
    subpath = named_profile(
        PROFILE_ID,
        "subpath",
        "https://gitlab.example/gitlab/api/v4/projects",
    )
    cache_username(workflow, PROFILE_ID, "alice")

    personal_pages.render_my_pages(workflow, [subpath], subpath, "my")

    assert [item[2]["arg"] for item in workflow.items] == [
        "https://gitlab.example/gitlab/alice",
        "https://gitlab.example/gitlab/dashboard/projects/starred",
        "https://gitlab.example/gitlab/dashboard/snippets",
        "https://gitlab.example/gitlab/dashboard/merge_requests",
        "https://gitlab.example/gitlab/dashboard/projects",
        "https://gitlab.example/gitlab/dashboard/issues",
        "https://gitlab.example/gitlab/-/profile/preferences",
        "https://gitlab.example/gitlab/",
        "https://gitlab.example/gitlab/dashboard/todos",
    ]


def test_personal_page_items_resolve_exact_urls_through_quick_open_graph():
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    cache_username(workflow, PROFILE_ID, "alice")
    personal_pages.render_my_pages(workflow, [first], first, "my")
    rows = {item[0]: index for index, item in enumerate(workflow.items)}

    dashboard_index = rows["my dashboard"]
    dashboard = workflow.items[dashboard_index]
    issues_index = rows["my issues"]
    issues = workflow.items[issues_index]

    assert _resolve_quick_open_graph(
        dashboard[2]["arg"], workflow.item_variables[dashboard_index]
    ) == "https://first.example/"
    assert _resolve_quick_open_graph(
        issues[2]["arg"], workflow.item_variables[issues_index]
    ) == "https://first.example/dashboard/issues"


def test_personal_page_url_flow_preserves_project_open_graph_behavior():
    assert _resolve_quick_open_graph(
        "https://gitlab.example/group/project",
        {"quick_open": "1"},
    ) == "https://gitlab.example/group/project/"
    assert _resolve_project_subpage_graph(
        "https://gitlab.example/group/project",
        "-/issues",
    ) == "https://gitlab.example/group/project/-/issues"


def test_render_my_pages_keeps_non_profile_pages_when_identity_is_unavailable():
    workflow = FakeWorkflow()
    first, _company = configured_profiles()

    personal_pages.render_my_pages(workflow, [first], first, "my")

    assert workflow.items[0] == (
        "my profile",
        "first · Profile unavailable; try again later",
        {"valid": False},
    )
    assert workflow.item_variables[0] == {}
    assert len(workflow.items) == 9
    assert all(item[2]["valid"] is True for item in workflow.items[1:])
    assert all(
        variables == {"quick_open": "1", "url_separator": ""}
        for variables in workflow.item_variables[1:]
    )


def test_render_my_pages_marks_unsupported_api_roots_invalid_and_sanitized():
    workflow = FakeWorkflow()
    unsupported = named_profile(
        PROFILE_ID,
        "broken",
        "https://user:secret@example.invalid/unexpected/path",
    )

    personal_pages.render_my_pages(workflow, [unsupported], unsupported, "my")

    assert len(workflow.items) == 9
    assert {item[1] for item in workflow.items} == {
        "broken · GitLab API URL is unsupported"
    }
    assert all(item[2] == {"valid": False} for item in workflow.items)
    assert all(variables == {} for variables in workflow.item_variables)
    assert "secret" not in repr(workflow.items)


@pytest.mark.parametrize(
    "api_url",
    [
        "https://user:secret@example.invalid/api/v4/projects",
        "ftp://example.invalid/api/v4/projects",
    ],
)
def test_render_my_pages_marks_unsafe_api_roots_invalid_and_sanitized(api_url):
    workflow = FakeWorkflow()
    unsafe = named_profile(PROFILE_ID, "broken", api_url)

    personal_pages.render_my_pages(workflow, [unsafe], unsafe, "my")

    assert len(workflow.items) == 9
    assert {item[1] for item in workflow.items} == {
        "broken · GitLab API URL is unsupported"
    }
    assert all(item[2] == {"valid": False} for item in workflow.items)
    assert all(variables == {} for variables in workflow.item_variables)
    assert "secret" not in repr(workflow.items)


def test_render_my_pages_warns_and_falls_back_when_selected_host_disappears():
    workflow = FakeWorkflow()
    first, _company = configured_profiles()
    cache_username(workflow, PROFILE_ID, "alice")

    personal_pages.render_my_pages(workflow, [first], first, "my removed ")

    assert workflow.items[0] == (
        "GitLab host is no longer configured",
        None,
        {"valid": False},
    )
    assert [item[0] for item in workflow.items[1:]] == [
        "my profile",
        "my starred projects",
        "my snippets",
        "my merge requests",
        "my projects",
        "my issues",
        "my preferences",
        "my dashboard",
        "my to-do list",
    ]
