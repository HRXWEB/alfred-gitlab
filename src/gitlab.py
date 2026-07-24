import argparse
import subprocess
import sys
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from cache_state import CacheState
from host_commands import add_host, remove_host, render_host_list
from host_values import InvalidApiUrlError, valid_api_url
from hosts import (
    ensure_profiles,
    get_default_profile,
    set_default_token,
    set_default_url,
)
from project_search import aggregate_projects, search_for_project
from workflow import ICON_INFO, ICON_WARNING, Workflow3

log = None
UPDATE_REPO = "HRXWEB/alfred-gitlab"
INSECURE_TRANSPORT_WARNING = (
    "GitLab API token transport is not encrypted over HTTP"
)


def project_web_url(project_url, api_url):
    configured_url = valid_api_url(api_url)
    if configured_url is None:
        return project_url

    try:
        ip_address(configured_url.hostname)
    except ValueError:
        project = urlsplit(project_url)
        return urlunsplit(
            (
                configured_url.scheme,
                configured_url.netloc,
                project.path,
                project.query,
                project.fragment,
            )
        )

    return project_url


def load_cached_projects(wf):
    profile = get_default_profile(wf)
    if profile is None:
        return None
    return CacheState(wf).load_projects(profile["id"])


def invalidate_default_projects(wf):
    profile = get_default_profile(wf)
    if profile is not None:
        CacheState(wf).invalidate_projects(profile["id"])


def main(wf):
    # build argument parser to parse script args and collect their
    # values
    parser = argparse.ArgumentParser()
    # add an optional (nargs='?') --setkey argument and save its
    # value to 'apikey' (dest). This will be called from a separate "Run Script"
    # action with the API key
    parser.add_argument("--setkey", dest="apikey", nargs="?", default=None)
    parser.add_argument("--seturl", dest="apiurl", nargs="?", default=None)
    parser.add_argument("--hostadd")
    parser.add_argument("--hostlist", action="store_true")
    parser.add_argument("--hostremove")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("query", nargs="?", default=None)
    # parse the script's arguments
    args = parser.parse_args(wf.args)

    ####################################################################
    # Save the provided API key or URL
    ####################################################################

    # decide what to do based on arguments
    if args.refresh:
        summary = subprocess.check_output(
            [
                sys.executable,
                wf.workflowfile("update.py"),
                "--all",
            ],
            universal_newlines=True,
        ).strip()
        print(summary)
        return 0

    cache = CacheState(wf)
    if args.hostadd is not None:
        _ = ensure_profiles(wf, cache)
        result = add_host(wf, cache, args.hostadd)
        if result.uses_http:
            log.warning(INSECURE_TRANSPORT_WARNING)
        print(result.message)
        return 0

    if args.hostremove is not None:
        _ = ensure_profiles(wf, cache)
        print(remove_host(wf, cache, args.hostremove))
        return 0

    if args.hostlist:
        profiles = ensure_profiles(wf, cache)
        render_host_list(wf, profiles, cache)
        wf.send_feedback()
        return 0

    if args.apikey:  # Script was passed an API key
        log.info("Setting API Key")
        set_default_token(wf, args.apikey, CacheState(wf))
        return 0  # 0 means script exited cleanly

    if args.apiurl:
        configured_url = valid_api_url(args.apiurl)
        if configured_url is None:
            raise InvalidApiUrlError
        log.info("Setting GitLab API URL")
        if configured_url.scheme == "http":
            log.warning(INSECURE_TRANSPORT_WARNING)
        set_default_url(wf, args.apiurl, CacheState(wf))
        return 0

    ####################################################################
    # Check that we have an API key saved
    ####################################################################

    profiles = ensure_profiles(wf, cache)
    if not profiles:
        wf.add_item(
            "No API key set.",
            "Please use glsetkey to set your GitLab API key.",
            valid=False,
            icon=ICON_WARNING,
        )
        wf.send_feedback()
        return 0

    ####################################################################
    # View/filter GitLab Projects
    ####################################################################

    query = args.query

    projects = aggregate_projects(wf, profiles, cache)

    if wf.update_available:
        # Add a notification to top of Script Filter results
        wf.add_item(
            "New version available",
            "Action this item to install the update",
            autocomplete="workflow:update",
            icon=ICON_INFO,
        )

    # If script was passed a query, use it to filter projects
    if query and projects:
        projects = wf.filter(query, projects, key=search_for_project, min_score=20)

    if not projects:  # we have no data to show, so show a warning and stop
        wf.add_item("No projects found", icon=ICON_WARNING)
        wf.send_feedback()
        return 0

    # Loop through the returned posts and add an item for each to
    # the list of results for Alfred
    for project in projects:
        wf.add_item(
            title=project["name_with_namespace"],
            subtitle=(
                "{} · {}".format(
                    project["_host_name"],
                    project["path_with_namespace"],
                )
            ),
            arg=project_web_url(project["web_url"], project["_api_url"]),
            valid=True,
            icon=None,
            uid=project["_alfred_uid"],
        )

    # Send the results to Alfred as XML
    wf.send_feedback()


if __name__ == "__main__":
    wf = Workflow3(
        update_settings={
            "github_slug": UPDATE_REPO,
        }
    )
    log = wf.logger
    sys.exit(wf.run(main))
