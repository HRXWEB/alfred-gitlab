import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import mureq
import personal_pages
from host_values import NameSource
from workflow import PasswordNotFound


PROFILE_ID = "a" * 32


class FakeWorkflow:
    def __init__(self):
        self.passwords = {}
        self.cached_values = {}
        self.cache_reads = []
        self.cache_writes = []
        self.items = []

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
