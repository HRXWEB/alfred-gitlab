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


def is_my_query(query: str | None) -> bool:
    parts = query.strip().split() if query is not None else []
    return bool(parts) and parts[0] == "my"


def render_my_pages(workflow, profiles, default_profile, query: str) -> None:
    profiles = tuple(profiles)
    if not profiles:
        return

    if default_profile is None:
        default_profile = profiles[0]

    parts = query.strip().split()
    requested_host = parts[1] if len(parts) > 1 else None
    selected_profile = next(
        (
            profile
            for profile in profiles
            if requested_host is not None and profile["name"] == requested_host
        ),
        None,
    )

    fully_selected_host_missing = (
        requested_host is not None
        and selected_profile is None
        and query[-1:].isspace()
    )
    if fully_selected_host_missing:
        workflow.add_item(
            "GitLab host is no longer configured",
            valid=False,
        )
        requested_host = None

    if selected_profile is not None:
        page_filter = " ".join(parts[2:])
        _render_page_rows(
            workflow,
            selected_profile,
            _filter_pages(workflow, page_filter),
        )
        return

    page_filter = "" if fully_selected_host_missing else " ".join(parts[1:])
    _render_page_rows(
        workflow,
        default_profile,
        _filter_pages(workflow, page_filter),
    )

    other_profiles = [
        profile
        for profile in profiles
        if profile["id"] != default_profile["id"]
    ]
    if page_filter:
        other_profiles = workflow.filter(
            page_filter,
            other_profiles,
            key=lambda profile: profile["name"],
            min_score=20,
        )
    for profile in other_profiles:
        name = profile["name"]
        workflow.add_item(
            title="my " + name,
            subtitle="Browse personal pages on " + name,
            autocomplete="my " + name + " ",
            valid=False,
        )


def _filter_pages(workflow, page_filter: str):
    if not page_filter:
        return PERSONAL_PAGES
    return workflow.filter(
        page_filter,
        PERSONAL_PAGES,
        key=lambda page: " ".join((page.key, page.title, page.subtitle)),
        min_score=20,
    )


def _render_page_rows(workflow, profile, pages) -> None:
    pages = tuple(pages)
    roots = derive_gitlab_roots(profile["api_url"])
    if roots is None:
        for page in pages:
            workflow.add_item(
                title="my " + page.key,
                subtitle=f"{profile['name']} · GitLab API URL is unsupported",
                valid=False,
            )
        return

    needs_username = any(page.needs_username for page in pages)
    username = resolve_username(workflow, profile, roots) if needs_username else None
    for page in pages:
        if page.needs_username and username is None:
            workflow.add_item(
                title="my " + page.key,
                subtitle=(
                    f"{profile['name']} · Profile unavailable; try again later"
                ),
                valid=False,
            )
            continue

        path = page.path.format(username=username)
        destination = roots.web_root.rstrip("/") + "/" + path.lstrip("/")
        item = workflow.add_item(
            title="my " + page.key,
            subtitle=f"{profile['name']} · {page.subtitle}",
            arg=destination,
            valid=True,
        )
        item.setvar("quick_open", "1")


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
