from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlunsplit

import mureq
from cache_records import CACHE_ERRORS, identity_cache_key
from host_values import valid_api_url
from hosts import token_account
from workflow import KeychainError


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
    is_page_filter = _matches_page_filter(parts[1:])
    is_host_prefix = requested_host is not None and any(
        profile["name"].casefold().startswith(requested_host.casefold())
        for profile in profiles
    )

    fully_selected_host_missing = (
        requested_host is not None
        and selected_profile is None
        and not is_page_filter
        and not is_host_prefix
        and (len(parts) > 2 or query[-1:].isspace())
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


def _matches_page_filter(parts) -> bool:
    query_terms = " ".join(parts).casefold().replace("-", " ").split()
    if not query_terms:
        return False
    for page in PERSONAL_PAGES:
        page_terms = (
            " ".join((page.key, page.title, page.subtitle))
            .casefold()
            .replace("-", " ")
            .split()
        )
        if all(
            any(page_term.startswith(query_term) for page_term in page_terms)
            for query_term in query_terms
        ):
            return True
    return False


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
        item.setvar("url_separator", "")


def derive_gitlab_roots(api_url: str) -> GitLabRoots | None:
    parsed = valid_api_url(api_url)
    suffix = "/api/v4/projects"
    if parsed is None or not parsed.path.endswith(suffix):
        return None

    web_path = parsed.path[: -len(suffix)]
    api_path = web_path + "/api/v4"
    return GitLabRoots(
        web_root=urlunsplit((parsed.scheme, parsed.netloc, web_path, "", "")),
        api_root=urlunsplit((parsed.scheme, parsed.netloc, api_path, "", "")),
    )


def resolve_username(workflow, profile, roots: GitLabRoots) -> str | None:
    try:
        profile_id = profile["id"]
        cache_key = identity_cache_key(profile_id)
    except (KeyError, TypeError, ValueError):
        return None

    try:
        cached = workflow.cached_data(cache_key, None, max_age=IDENTITY_CACHE_AGE)
    except CACHE_ERRORS:
        _discard_identity_cache(workflow, cache_key)
    except OSError:
        pass
    else:
        if isinstance(cached, str) and cached:
            return cached

    try:
        token = workflow.get_password(token_account(profile_id))
        response = mureq.get(
            roots.api_root.rstrip("/") + "/user",
            headers={"PRIVATE-TOKEN": token},
        )
        response.raise_for_status()
        username = response.json()["username"]
        if not isinstance(username, str) or not username:
            return None
    except (
        KeychainError,
        mureq.HTTPException,
        OSError,
        TypeError,
        KeyError,
        *CACHE_ERRORS,
    ):
        return None

    try:
        workflow.cache_data(cache_key, username)
    except (OSError, *CACHE_ERRORS):
        pass
    return username


def _discard_identity_cache(workflow, cache_key: str) -> None:
    try:
        workflow.cache_data(cache_key, None)
    except (OSError, *CACHE_ERRORS):
        pass
