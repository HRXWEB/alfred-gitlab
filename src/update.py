import mureq
from cache_state import CacheState
from hosts import get_default_profile
from workflow import PasswordNotFound, Workflow


def get_projects(api_key, url):
    return get_project_page(api_key, url, 1, [])


def get_project_page(api_key, url, page, stored_projects):
    log.info(f"Calling API page {page}")
    response = mureq.get(
        url,
        headers={"PRIVATE-TOKEN": api_key},
        params={"per_page": 100, "page": page, "membership": "true"},
    )

    # throw an error if request failed
    # Workflow will catch this and show it to the user
    response.raise_for_status()

    # Parse the JSON returned by GitLab and extract the projects
    projects = stored_projects + response.json()

    next_page = response.headers.get("X-Next-Page")
    if next_page:
        projects = get_project_page(api_key, url, next_page, projects)

    return projects


def main(wf):
    try:
        profile = get_default_profile(wf)
        if profile is None:
            return
        cache = CacheState(wf)
        generation = cache.current_generation(profile["id"])
        # Get API key from Keychain
        api_key = wf.get_password("gitlab_api_key")
        api_url = wf.settings.get("api_url", "https://gitlab.com/api/v4/projects")
        projects = get_projects(api_key, api_url)
        stored = cache.store_projects(profile["id"], generation, projects)

        # Record our progress in the log file
        if stored:
            log.debug(f"{len(projects)} gitlab projects cached")
        else:
            log.info("Discarded projects fetched with outdated settings")

    except PasswordNotFound:  # API key has not yet been set
        wf.logger.error("No API key saved")
        raise


if __name__ == "__main__":
    wf = Workflow()
    log = wf.logger
    raise SystemExit(wf.run(main))
