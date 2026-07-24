import argparse
import subprocess
import sys
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from cache_state import CacheState, projects_key
from hosts import get_default_profile, valid_api_url
from workflow import ICON_INFO, ICON_WARNING, PasswordNotFound, Workflow3
from workflow.background import is_running, run_in_background

log = None
UPDATE_REPO = "HRXWEB/alfred-gitlab"


def search_for_project(project):
    """Generate a string search key for a project"""
    elements = [project["name_with_namespace"], project["path_with_namespace"]]
    return " ".join(elements)


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
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("query", nargs="?", default=None)
    # parse the script's arguments
    args = parser.parse_args(wf.args)

    ####################################################################
    # Save the provided API key or URL
    ####################################################################

    # decide what to do based on arguments
    if args.refresh:
        log.info("Refreshing GitLab projects")
        invalidate_default_projects(wf)
        subprocess.check_call([sys.executable, wf.workflowfile("update.py")])
        return 0

    if args.apikey:  # Script was passed an API key
        log.info("Setting API Key")
        wf.save_password("gitlab_api_key", args.apikey)
        invalidate_default_projects(wf)
        return 0  # 0 means script exited cleanly

    if args.apiurl:
        configured_url = valid_api_url(args.apiurl)
        if configured_url is None:
            raise ValueError("GitLab API URL must be an absolute HTTP(S) URL")
        log.info("Setting GitLab API URL")
        if configured_url.scheme == "http":
            log.warning("GitLab API token transport is not encrypted over HTTP")
        wf.settings["api_url"] = args.apiurl
        invalidate_default_projects(wf)
        return 0

    ####################################################################
    # Check that we have an API key saved
    ####################################################################

    try:
        wf.get_password("gitlab_api_key")
    except PasswordNotFound:  # API key has not yet been set
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

    projects = load_cached_projects(wf)

    if wf.update_available:
        # Add a notification to top of Script Filter results
        wf.add_item(
            "New version available",
            "Action this item to install the update",
            autocomplete="workflow:update",
            icon=ICON_INFO,
        )

    # Notify the user if the cache is being updated
    if is_running("update") and not projects:
        wf.rerun = 0.5
        wf.add_item(
            "Updating project list via GitLab...",
            subtitle="This can take some time if you have a large number of projects.",
            valid=False,
            icon=ICON_INFO,
        )

    # Start update script if cached data is too old (or doesn't exist)
    profile = get_default_profile(wf)
    project_cache_key = (
        projects_key(profile["id"]) if profile is not None else "projects"
    )
    if not wf.cached_data_fresh(project_cache_key, max_age=3600) and not is_running(
        "update"
    ):
        cmd = [sys.executable, wf.workflowfile("update.py")]
        run_in_background("update", cmd)
        wf.rerun = 0.5

    # If script was passed a query, use it to filter projects
    if query and projects:
        projects = wf.filter(query, projects, key=search_for_project, min_score=20)

    if not projects:  # we have no data to show, so show a warning and stop
        wf.add_item("No projects found", icon=ICON_WARNING)
        wf.send_feedback()
        return 0

    # Loop through the returned posts and add an item for each to
    # the list of results for Alfred
    api_url = wf.settings.get("api_url", "https://gitlab.com/api/v4/projects")

    for project in projects:
        wf.add_item(
            title=project["name_with_namespace"],
            subtitle=project["path_with_namespace"],
            arg=project_web_url(project["web_url"], api_url),
            valid=True,
            icon=None,
            uid=project["id"],
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
