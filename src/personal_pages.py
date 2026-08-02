from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import mureq
from hosts import token_account
from workflow import PasswordNotFound


IDENTITY_CACHE_AGE: Final = 86400

@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class GitLabRoots:
    web_root: str
    api_root: str


@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class PersonalPage:
    key: str
    title: str
    subtitle: str
    path: str
    needs_username: bool = False


PERSONAL_PAGES: Final = (
    PersonalPage(
        key="profile",
        title="Profile",
        subtitle="View your public user profile",
        path="/{username}",
        needs_username=True,
    ),
    PersonalPage(
        key="starred projects",
        title="Starred projects",
        subtitle="View your starred projects",
        path="/dashboard/projects/starred",
    ),
    PersonalPage(
        key="snippets",
        title="Snippets",
        subtitle="View your snippets",
        path="/dashboard/snippets",
    ),
    PersonalPage(
        key="merge requests",
        title="Merge requests",
        subtitle="View your merge requests",
        path="/dashboard/merge_requests",
    ),
    PersonalPage(
        key="projects",
        title="Projects",
        subtitle="View your projects",
        path="/dashboard/projects",
    ),
    PersonalPage(
        key="issues",
        title="Issues",
        subtitle="View your issues",
        path="/dashboard/issues",
    ),
    PersonalPage(
        key="preferences",
        title="Preferences",
        subtitle="Edit your preferences",
        path="/-/profile/preferences",
    ),
    PersonalPage(
        key="dashboard",
        title="Dashboard",
        subtitle="View your dashboard",
        path="/",
    ),
    PersonalPage(
        key="to-do list",
        title="To-do list",
        subtitle="View your to-do list",
        path="/dashboard/todos",
    ),
)


def derive_gitlab_roots(api_url: str) -> GitLabRoots | None:
    try:
        parsed = urlsplit(api_url)
    except (TypeError, ValueError):
        return None
    suffix = "/api/v4/projects"
    if not parsed.scheme or not parsed.netloc or not parsed.path.endswith(suffix):
        return None

    web_path = parsed.path[: -len(suffix)]
    api_path = web_path + "/api/v4"
    return GitLabRoots(
        web_root=urlunsplit((parsed.scheme, parsed.netloc, web_path, "", "")),
        api_root=urlunsplit((parsed.scheme, parsed.netloc, api_path, "", "")),
    )


def identity_cache_key(profile_id: str) -> str:
    return "gitlab-username-" + profile_id


def resolve_username(workflow, profile, roots: GitLabRoots) -> str | None:
    try:
        profile_id = profile["id"]
        cache_key = identity_cache_key(profile_id)
        cached = workflow.cached_data(cache_key, None, max_age=IDENTITY_CACHE_AGE)
        if isinstance(cached, str) and cached:
            return cached

        token = workflow.get_password(token_account(profile_id))
        response = mureq.get(
            roots.api_root.rstrip("/") + "/user",
            headers={"PRIVATE-TOKEN": token},
        )
        response.raise_for_status()
        username = response.json()["username"]
        if not isinstance(username, str) or not username:
            return None
        workflow.cache_data(cache_key, username)
        return username
    except (
        PasswordNotFound,
        mureq.HTTPException,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ):
        return None
