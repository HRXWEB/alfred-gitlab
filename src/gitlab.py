# encoding: utf-8

import sys
import argparse
import pickle
import re
import subprocess
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from workflow import Workflow3, ICON_WARNING, ICON_INFO, PasswordNotFound
from workflow.background import run_in_background, is_running
from cache_state import invalidate_projects

log = None
UPDATE_REPO = 'HRXWEB/alfred-gitlab'
DOMAIN_LABEL = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')
CACHE_ERRORS = (
    pickle.UnpicklingError,
    EOFError,
    AttributeError,
    ImportError,
    IndexError,
    OverflowError,
    UnicodeDecodeError,
    ValueError,
)


def search_for_project(project):
    """Generate a string search key for a project"""
    elements = [project['name_with_namespace'], project['path_with_namespace']]
    return u' '.join(elements)


def valid_api_url(api_url):
    try:
        configured_url = urlsplit(api_url)
        hostname = configured_url.hostname
        configured_url.port
    except (TypeError, ValueError):
        return None

    if (
        configured_url.scheme not in ('http', 'https')
        or not hostname
        or configured_url.username
        or configured_url.password
    ):
        return None

    try:
        ip_address(hostname)
    except ValueError:
        domain = hostname.rstrip('.')
        if (
            len(domain) > 253
            or not all(DOMAIN_LABEL.match(label) for label in domain.split('.'))
        ):
            return None

    return configured_url


def project_web_url(project_url, api_url):
    configured_url = valid_api_url(api_url)
    if configured_url is None:
        return project_url

    try:
        ip_address(configured_url.hostname)
    except ValueError:
        project = urlsplit(project_url)
        return urlunsplit((
            configured_url.scheme,
            configured_url.netloc,
            project.path,
            project.query,
            project.fragment,
        ))

    return project_url


def load_cached_projects(wf):
    try:
        projects = wf.cached_data('projects', None, max_age=0)
    except CACHE_ERRORS:
        log.warning("Discarding corrupt GitLab project cache")
        invalidate_projects(wf)
        return None
    if projects is not None and not isinstance(projects, list):
        log.warning("Discarding invalid GitLab project cache")
        invalidate_projects(wf)
        return None
    return projects


def main(wf):
    # build argument parser to parse script args and collect their
    # values
    parser = argparse.ArgumentParser()
    # add an optional (nargs='?') --setkey argument and save its
    # value to 'apikey' (dest). This will be called from a separate "Run Script"
    # action with the API key
    parser.add_argument('--setkey', dest='apikey', nargs='?', default=None)
    parser.add_argument('--seturl', dest='apiurl', nargs='?', default=None)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('query', nargs='?', default=None)
    # parse the script's arguments
    args = parser.parse_args(wf.args)

    ####################################################################
    # Save the provided API key or URL
    ####################################################################

    # decide what to do based on arguments
    if args.refresh:
        log.info("Refreshing GitLab projects")
        invalidate_projects(wf)
        subprocess.check_call(
            [sys.executable, wf.workflowfile('update.py')])
        return 0

    if args.apikey:  # Script was passed an API key
        log.info("Setting API Key")
        wf.save_password('gitlab_api_key', args.apikey)
        invalidate_projects(wf)
        return 0  # 0 means script exited cleanly

    if args.apiurl:
        configured_url = valid_api_url(args.apiurl)
        if configured_url is None:
            raise ValueError('GitLab API URL must be an absolute HTTP(S) URL')
        log.info("Setting GitLab API URL")
        if configured_url.scheme == 'http':
            log.warning("GitLab API token transport is not encrypted over HTTP")
        wf.settings['api_url'] = args.apiurl
        invalidate_projects(wf)
        return 0

    ####################################################################
    # Check that we have an API key saved
    ####################################################################

    try:
        wf.get_password('gitlab_api_key')
    except PasswordNotFound:  # API key has not yet been set
        wf.add_item('No API key set.',
                    'Please use glsetkey to set your GitLab API key.',
                    valid=False,
                    icon=ICON_WARNING)
        wf.send_feedback()
        return 0

    ####################################################################
    # View/filter GitLab Projects
    ####################################################################

    query = args.query

    projects = load_cached_projects(wf)

    if wf.update_available:
        # Add a notification to top of Script Filter results
        wf.add_item('New version available',
                    'Action this item to install the update',
                    autocomplete='workflow:update',
                    icon=ICON_INFO)

    # Notify the user if the cache is being updated
    if is_running('update') and not projects:
        wf.rerun = 0.5
        wf.add_item('Updating project list via GitLab...',
                    subtitle=u'This can take some time if you have a large number of projects.',
                    valid=False,
                    icon=ICON_INFO)

    # Start update script if cached data is too old (or doesn't exist)
    if not wf.cached_data_fresh('projects', max_age=3600) and not is_running('update'):
        cmd = [sys.executable, wf.workflowfile('update.py')]
        run_in_background('update', cmd)
        wf.rerun = 0.5

    # If script was passed a query, use it to filter projects
    if query and projects:
        projects = wf.filter(query, projects, key=search_for_project, min_score=20)

    if not projects:  # we have no data to show, so show a warning and stop
        wf.add_item('No projects found', icon=ICON_WARNING)
        wf.send_feedback()
        return 0

    # Loop through the returned posts and add an item for each to
    # the list of results for Alfred
    api_url = wf.settings.get(
        'api_url', 'https://gitlab.com/api/v4/projects')

    for project in projects:
        wf.add_item(title=project['name_with_namespace'],
                    subtitle=project['path_with_namespace'],
                    arg=project_web_url(project['web_url'], api_url),
                    valid=True,
                    icon=None,
                    uid=project['id'])

    # Send the results to Alfred as XML
    wf.send_feedback()


if __name__ == u"__main__":
    wf = Workflow3(update_settings={
        'github_slug': UPDATE_REPO,
    })
    log = wf.logger
    sys.exit(wf.run(main))
