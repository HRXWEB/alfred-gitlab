# Personal Pages and Default Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `glhostdefault`, a default-first multi-host `gl my` personal-page menu, and publish the verified workflow as `v4.2.0`.

**Architecture:** Keep `gitlab.py` as the Alfred entry point, add registry-backed default-host selection, and place personal-page parsing, URL derivation, identity caching, and menu rendering in a focused `personal_pages.py` module. Dynamic Script Filter rows carry stable profile IDs for mutations and set `quick_open=1` on page rows so they bypass the existing repository sub-page chooser.

**Tech Stack:** Python 3.9-compatible source, Alfred Workflow 3 JSON feedback, plist workflow graph, vendored `mureq`, pytest, Bash/Zip build script, GitHub CLI.

## Global Constraints

- Preserve aggregate `gl <query>` project search for every query whose first complete token is not `my`.
- Preserve credentials, project caches, refresh status, and configured host order when the default changes.
- Support GitLab.com, self-managed domains, explicit ports, IPv4, bracketed IPv6, and GitLab deployment subpaths.
- Never expose tokens, API response bodies, or internal exception details in Alfred feedback or new logs.
- Keep all new runtime source compatible with the project's Python 3.9 checks and add no dependency.
- Use the GitLab terms Profile, Starred projects, Snippets, Merge requests, Projects, Issues, Preferences, Dashboard, and To-do list in that order.
- Publish only `v4.2.0` to `HRXWEB/alfred-gitlab`, and only after the reviewed feature branch is merged through a GitHub PR and the tag and GitHub Release are confirmed not to exist.

## File Structure

- `src/host_registry.py`: atomically persist a selected default profile ID.
- `src/hosts.py`: export the default-selection operation and its error type.
- `src/host_commands.py`: render the `glhostdefault` chooser and format its mutation result.
- `src/personal_pages.py`: derive GitLab roots, cache the authenticated username, parse `my` queries, and render page/host rows.
- `src/gitlab.py`: dispatch the new CLI flags and exact-token `my` queries.
- `src/info.plist`: wire the `glhostdefault` filter/action/notification and advance workflow metadata.
- `tests/test_hosts.py`: registry rollback and default-selection tests.
- `tests/test_gitlab.py`: host chooser, `my` dispatch, plist graph, and version tests.
- `tests/test_personal_pages.py`: URL, identity, ordering, filtering, and degraded-mode tests.
- `tests/test_python_compatibility.py`: include `personal_pages.py` in the Python 3.9 source scan.
- `tests/test_build.py`: assert the release archive contains the new runtime module.
- `README.md`, `src/info.plist`, `CHANGELOG.md`: user-facing commands, interaction, mappings, and `v4.2.0` notes.
- `GitLab.alfredworkflow`: deterministic release artifact rebuilt from committed `src/` blobs.

---

### Task 1: Atomic Default-Host Selection

**Files:**
- Modify: `src/host_registry.py`
- Modify: `src/hosts.py`
- Modify: `src/host_commands.py`
- Test: `tests/test_hosts.py`

**Interfaces:**
- Consumes: `HostRegistry.profiles()`, `_save_profiles()`, `snapshot_registry_settings()`, and `restore_registry_settings()`.
- Produces: `UnknownHostProfileIdError`, `HostRegistry.set_default(profile_id: str) -> HostProfile`, `set_default_profile(registry: HostRegistry, profile_id: str) -> ProfileRecord`, `set_default_host(workflow, profile_id: str) -> str`, and `render_default_host_list(workflow, profiles, default_profile_id) -> None`.

- [ ] **Step 1: Write failing registry tests**

Add tests that select the second of two profiles, preserve all other settings,
reject an unknown ID without mutation, and restore the former ID when a
partially mutating settings store raises:

```python
def test_set_default_profile_selects_known_id_without_reordering():
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    first = hosts.add_or_update_profile(registry, host_values.ProfileDraft(
        name="first", api_url="https://first.example/api/v4/projects",
        token="first-token",
    ))
    second = hosts.add_or_update_profile(registry, host_values.ProfileDraft(
        name="second", api_url="https://second.example/api/v4/projects",
        token="second-token",
    ))

    selected = hosts.set_default_profile(registry, second["id"])

    assert selected == second
    assert hosts.get_profiles(workflow) == [first, second]
    assert workflow.settings["default_host_id"] == second["id"]


def test_set_default_profile_rejects_unknown_id_without_mutation():
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    hosts.add_or_update_profile(registry, host_values.ProfileDraft(
        name="first", api_url="https://first.example/api/v4/projects",
        token="first-token",
    ))
    original = dict(workflow.settings)

    with pytest.raises(hosts.UnknownHostProfileIdError):
        hosts.set_default_profile(registry, "missing-id")

    assert workflow.settings == original
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest -q tests/test_hosts.py -k 'set_default_profile'`

Expected: FAIL because `set_default_profile` and
`UnknownHostProfileIdError` do not exist.

- [ ] **Step 3: Implement atomic registry selection**

Add the ID-specific exception and method. Snapshot before `_save_profiles()`
and restore on `OSError`, `AcquisitionError`, or `KeychainError`:

```python
@dataclass(frozen=True)  #noqa: SLOTS_OK - Python 3.9 workflow runtime
class UnknownHostProfileIdError(ValueError):
    profile_id: str

    def __str__(self) -> str:
        return "Unknown GitLab host profile ID"


def set_default(self, profile_id: str) -> HostProfile:
    profiles = self.profiles()
    selected = next(
        (profile for profile in profiles if profile.id == profile_id), None
    )
    if selected is None:
        raise UnknownHostProfileIdError(profile_id)
    snapshot = snapshot_registry_settings(
        self.workflow, [profile.to_record() for profile in profiles]
    )
    try:
        _save_profiles(self.workflow, profiles, selected.id)
    except (OSError, AcquisitionError, KeychainError):
        _ = restore_registry_settings(self.workflow, snapshot)
        raise
    return selected
```

Export a record-returning wrapper from `hosts.py`. In `host_commands.py`, add
`set_default_host()` returning `Default host set to <name>` and
`render_default_host_list()`. Render the current profile first with
`valid=False`; render each other profile with `arg=profile["id"]` and
`valid=True`.

- [ ] **Step 4: Add chooser rendering tests**

Use a two-profile `FakeWorkflow` and assert exact row order and payloads:

```python
assert workflow.items == [
    ("first", "Current default · https://first.example/api/v4/projects",
     {"valid": False}),
    ("second", "Set as default · https://second.example/api/v4/projects",
     {"arg": second["id"], "valid": True}),
]
```

Also assert `set_default_host()` returns `Default host set to second`.

- [ ] **Step 5: Run host tests**

Run: `pytest -q tests/test_hosts.py`

Expected: all host tests PASS.

- [ ] **Step 6: Commit the registry slice**

```bash
git add src/host_registry.py src/hosts.py src/host_commands.py tests/test_hosts.py
git commit -m "feat(hosts): allow selecting the default host"
```

---

### Task 2: GitLab Personal-Page Roots and Identity Cache

**Files:**
- Create: `src/personal_pages.py`
- Create: `tests/test_personal_pages.py`
- Modify: `tests/test_python_compatibility.py`

**Interfaces:**
- Consumes: `ProfileRecord`, `token_account(profile_id)`, `mureq.get()`, and Workflow methods `cached_data`, `cache_data`, `get_password`, `add_item`, and `filter`.
- Produces: `GitLabRoots`, `PersonalPage`, `derive_gitlab_roots(api_url: str) -> GitLabRoots | None`, `identity_cache_key(profile_id: str) -> str`, and `resolve_username(workflow, profile, roots) -> str | None`.

- [ ] **Step 1: Write URL derivation tests**

Create parameterized coverage with exact expected roots:

```python
@pytest.mark.parametrize(("api_url", "web_root", "api_root"), [
    ("https://gitlab.com/api/v4/projects",
     "https://gitlab.com", "https://gitlab.com/api/v4"),
    ("https://gitlab.example:8443/gitlab/api/v4/projects",
     "https://gitlab.example:8443/gitlab",
     "https://gitlab.example:8443/gitlab/api/v4"),
    ("http://192.0.2.10:8080/api/v4/projects",
     "http://192.0.2.10:8080", "http://192.0.2.10:8080/api/v4"),
    ("http://[2001:db8::10]:8080/api/v4/projects",
     "http://[2001:db8::10]:8080",
     "http://[2001:db8::10]:8080/api/v4"),
])
def test_derive_gitlab_roots(api_url, web_root, api_root):
    assert personal_pages.derive_gitlab_roots(api_url) == (
        personal_pages.GitLabRoots(web_root=web_root, api_root=api_root)
    )
```

Reject a valid absolute URL whose path does not end in `/api/v4/projects`.

- [ ] **Step 2: Run URL tests and verify failure**

Run: `pytest -q tests/test_personal_pages.py -k 'derive_gitlab_roots'`

Expected: collection FAIL because `personal_pages` does not exist.

- [ ] **Step 3: Implement root derivation and page metadata**

Create frozen dataclasses and preserve the parsed authority/subpath with
`urlsplit()`/`urlunsplit()`:

```python
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
```

Define `PERSONAL_PAGES` in the specified nine-item order. Build subpath-safe
destinations with `root.rstrip("/") + "/" + path.lstrip("/")` rather than
`urljoin()`, which would discard a deployment subpath.

- [ ] **Step 4: Write identity cache tests**

Use a fake workflow and monkeypatched `mureq.get` to prove:

```python
assert calls == [(
    "https://gitlab.example/api/v4/user",
    {"PRIVATE-TOKEN": "secret-token"},
)]
assert workflow.cache_writes == [("gitlab-username-" + PROFILE_ID, "alice")]
assert resolve_username(workflow, profile, roots) == "alice"
assert len(calls) == 1
```

Add failure cases for a missing token, HTTP failure, malformed JSON, and a
missing/empty username. Each must return `None` without leaking the exception
text into workflow items or printed output.

- [ ] **Step 5: Implement cached username resolution**

Use a 24-hour cache (`IDENTITY_CACHE_AGE = 86400`). Read a cached non-empty
string before accessing Keychain or network. Fetch `<api_root>/user` with the
existing `PRIVATE-TOKEN` header, call `raise_for_status()`, validate a non-empty
string `username`, and cache only that string. Catch the bounded sanitized set
`(PasswordNotFound, mureq.HTTPException, OSError, ValueError, TypeError,
KeyError)` and return `None`.

- [ ] **Step 6: Add Python 3.9 scan coverage and run tests**

Append `"personal_pages.py"` to `V4_MODULES` in
`tests/test_python_compatibility.py`.

Run: `pytest -q tests/test_personal_pages.py tests/test_python_compatibility.py`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit the URL and identity slice**

```bash
git add src/personal_pages.py tests/test_personal_pages.py tests/test_python_compatibility.py
git commit -m "feat(my): add GitLab personal page metadata"
```

---

### Task 3: Default-First `gl my` Rendering and Dispatch

**Files:**
- Modify: `src/personal_pages.py`
- Modify: `src/gitlab.py`
- Modify: `tests/test_personal_pages.py`
- Modify: `tests/test_gitlab.py`

**Interfaces:**
- Consumes: Task 2's `PERSONAL_PAGES`, `derive_gitlab_roots()`, and `resolve_username()` plus `get_default_profile()` and configured `ProfileRecord` values.
- Produces: `is_my_query(query: str | None) -> bool`, `render_my_pages(workflow, profiles, default_profile, query: str) -> None`, and an early-return branch in `gitlab.main()`.

- [ ] **Step 1: Write exact-token dispatch tests**

Monkeypatch `gitlab.render_my_pages` and project aggregation, then assert:

```python
@pytest.mark.parametrize("query", ["my", "my ", "my issues", "my company issues"])
def test_my_query_dispatches_without_project_search(query, monkeypatch):
    workflow = FakeWorkflow([query])
    rendered = []
    monkeypatch.setattr(gitlab, "render_my_pages",
                        lambda *args: rendered.append(args))
    monkeypatch.setattr(gitlab, "aggregate_projects",
                        lambda *args: pytest.fail("project search ran"))
    gitlab.main(workflow)
    assert len(rendered) == 1
    assert workflow.feedback_count == 1


@pytest.mark.parametrize("query", ["myproject", "my-company", "mystery"])
def test_my_prefix_remains_project_search(query, monkeypatch):
    workflow = FakeWorkflow([query])
    monkeypatch.setattr(gitlab, "aggregate_projects", lambda *args: [])
    gitlab.main(workflow)
    assert workflow.filter_calls == [(query, 20)] or workflow.items
```

- [ ] **Step 2: Run dispatch tests and verify failure**

Run: `pytest -q tests/test_gitlab.py -k 'my_query or my_prefix'`

Expected: FAIL because no `my` branch exists.

- [ ] **Step 3: Add the early `my` branch**

After profiles are ensured and authentication is established, but before
aggregate project refresh/search, add:

```python
if is_my_query(query):
    render_my_pages(wf, profiles, get_default_profile(wf), query or "")
    wf.send_feedback()
    return 0
```

The predicate must use `query.strip().split()` and require `parts[0] == "my"`.

- [ ] **Step 4: Write menu ordering and navigation tests**

For default `first` and non-default `company`, assert that `gl my` produces
nine page rows followed by one host row. Check the first page item and host row:

```python
assert workflow.items[0][0:2] == (
    "my profile", "first · View your public user profile"
)
assert workflow.items[0][2]["arg"] == "https://first.example/alice"
assert workflow.items[0][2]["valid"] is True
assert workflow.item_variables[0]["quick_open"] == "1"
assert workflow.items[9] == (
    "my company",
    "Browse personal pages on company",
    {"autocomplete": "my company ", "valid": False},
)
```

Add tests that `my company ` shows exactly nine company rows, that
`my company issues` shows only Issues, and that a single-host setup has no host
navigation rows.

- [ ] **Step 5: Implement query parsing and rendering**

Use an exact configured host-name match after `my` to enter the second level.
Otherwise render/filter the default pages and append filtered non-default host
rows. For every actionable page item:

```python
item = workflow.add_item(
    title="my " + page.key,
    subtitle=f"{profile['name']} · {page.subtitle}",
    arg=destination,
    valid=True,
)
item.setvar("quick_open", "1")
```

If roots are unsupported, render all page rows invalid with a sanitized API URL
message. If identity resolution fails, render Profile invalid with
`<host> · Profile unavailable; try again later` while leaving eight rows valid.
When a fully selected host disappears, prepend an invalid
`GitLab host is no longer configured` warning and fall back to the default menu.

- [ ] **Step 6: Run focused and regression tests**

Run: `pytest -q tests/test_personal_pages.py tests/test_gitlab.py`

Expected: all selected tests PASS, including existing aggregate search tests.

- [ ] **Step 7: Commit the `gl my` slice**

```bash
git add src/personal_pages.py src/gitlab.py tests/test_personal_pages.py tests/test_gitlab.py
git commit -m "feat(my): add default-first personal page navigation"
```

---

### Task 4: `glhostdefault` Alfred Workflow Wiring

**Files:**
- Modify: `src/gitlab.py`
- Modify: `src/info.plist`
- Modify: `tests/test_gitlab.py`

**Interfaces:**
- Consumes: Task 1's `render_default_host_list()` and `set_default_host()`.
- Produces: CLI flags `--hostdefault-list` and `--set-default-host <profile-id>` plus a connected Alfred Script Filter, Run Script action, and notification.

- [ ] **Step 1: Write CLI and plist graph tests**

Add tests for list rendering, mutation output, and exact wiring:

```python
def test_hostdefault_list_renders_feedback():
    workflow = FakeWorkflow(["--hostdefault-list"])
    gitlab.main(workflow)
    assert workflow.feedback_count == 1
    assert workflow.items[0][1].startswith("Current default · ")


def test_hostdefault_action_prints_selected_name(capsys):
    workflow = FakeWorkflow(["--set-default-host", PROFILE_ID])
    gitlab.main(workflow)
    assert capsys.readouterr().out == "Default host set to gitlab.example.com\n"


def test_hostdefault_workflow_connection():
    workflow = load_plist()
    chooser = next(item for item in workflow["objects"]
                   if item.get("config", {}).get("keyword") == "glhostdefault")
    assert chooser["type"] == "alfred.workflow.input.scriptfilter"
    assert chooser["config"]["script"] == "python3 gitlab.py --hostdefault-list"
    action_uid = workflow["connections"][chooser["uid"]][0]["destinationuid"]
    action = next(item for item in workflow["objects"] if item["uid"] == action_uid)
    assert action["config"]["script"] == (
        'python3 gitlab.py --set-default-host "{query}"'
    )
```

Follow the action connection and assert the notification text is `{query}`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest -q tests/test_gitlab.py -k 'hostdefault'`

Expected: FAIL because the parser flags and plist objects do not exist.

- [ ] **Step 3: Implement CLI dispatch**

Add parser arguments and handle them alongside existing host-management flags:

```python
parser.add_argument("--hostdefault-list", action="store_true")
parser.add_argument("--set-default-host")
```

Both paths call `ensure_profiles()` first. The list path sends feedback; the
mutation path prints the sanitized success message and returns zero.

- [ ] **Step 4: Add the plist nodes and connections**

Add three unique UIDs and their connections:

- Script Filter keyword `glhostdefault`, no argument, script
  `python3 gitlab.py --hostdefault-list`.
- Run Script action `python3 gitlab.py --set-default-host "{query}"`.
- Notification title `Default GitLab Host Changed`, text `{query}`.

Add matching `uidata` coordinates and keep every existing connection intact.
Use `plutil -lint src/info.plist` after editing.

- [ ] **Step 5: Run CLI, plist, and graph tests**

Run: `pytest -q tests/test_gitlab.py -k 'hostdefault or workflow_graph'`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the Alfred wiring slice**

```bash
git add src/gitlab.py src/info.plist tests/test_gitlab.py
git commit -m "feat(hosts): add glhostdefault workflow command"
```

---

### Task 5: Documentation, Version, and Release Artifact

**Files:**
- Modify: `README.md`
- Modify: `src/info.plist`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_gitlab.py`
- Modify: `tests/test_build.py`
- Modify: `GitLab.alfredworkflow`

**Interfaces:**
- Consumes: all completed runtime behavior and `scripts/build.sh`.
- Produces: documented `v4.2.0` and a verified deterministic workflow archive ready for review and release.

- [ ] **Step 1: Write release metadata and archive-content tests**

Change the metadata expectation to `4.2.0` and extend the build test:

```python
assert workflow["version"] == "4.2.0"

with zipfile.ZipFile(first) as archive:
    assert "personal_pages.py" in archive.namelist()
    assert plistlib.loads(archive.read("info.plist"))["version"] == "4.2.0"
```

- [ ] **Step 2: Run release tests and verify failure**

Run: `pytest -q tests/test_gitlab.py::test_v4_workflow_metadata tests/test_build.py`

Expected: FAIL because source metadata is still `4.1.0` and the committed build
does not yet contain `personal_pages.py`.

- [ ] **Step 3: Update documentation and release metadata**

In both README locations (`README.md` and the embedded `readme` string in
`src/info.plist`), document:

```text
glhostdefault
gl my
gl my <host-name> <page-filter>
```

Describe the default-first nine-row menu, non-default host drill-down, page
mappings, and that `glhostdefault` changes the host used by compatibility
commands. Set plist version to `4.2.0`. Move the changelog additions into:

```markdown
## [v4.2.0] (2026-08-02)
* Add `glhostdefault` to select the default GitLab profile
* Add default-first `gl my` navigation for personal GitLab pages
* Add multi-host drill-down with GitLab-specific snippets, merge requests, and to-do mappings
```

Update comparison links so `[Unreleased]` starts at `v4.2.0` and add the
`[v4.2.0]` comparison from `v4.1.0`.

- [ ] **Step 4: Run complete verification before building**

Run:

```bash
plutil -lint src/info.plist
python3 -m pytest -q
git diff --check
```

Expected: valid plist, all tests PASS, and no whitespace errors.

- [ ] **Step 5: Commit source and documentation metadata**

```bash
git add README.md CHANGELOG.md src/info.plist tests/test_gitlab.py tests/test_build.py
git commit -m "docs: prepare v4.2.0 release"
```

- [ ] **Step 6: Build and inspect the release artifact**

Build from committed source and inspect exact contents:

```bash
./scripts/build.sh
unzip -t GitLab.alfredworkflow
unzip -l GitLab.alfredworkflow
unzip -p GitLab.alfredworkflow info.plist | plutil -extract version raw -
```

Expected: archive integrity succeeds, `personal_pages.py` is listed, and the
embedded version is `4.2.0`.

- [ ] **Step 7: Commit the deterministic artifact**

```bash
git add GitLab.alfredworkflow
git commit -m "build: package v4.2.0 workflow"
```

- [ ] **Step 8: Re-run final verification on the exact candidate commit**

Run:

```bash
python3 -m pytest -q
git status --short
git log -1 --oneline
git remote get-url origin
```

Expected: tests PASS, worktree clean, origin resolves to
`HRXWEB/alfred-gitlab`, and the candidate commit contains the complete source,
documentation, metadata, tests, and archive. Publishing happens only after the
whole-branch review and PR merge described below.

## Post-Implementation Integration and Release

After all five tasks pass their scoped reviews, run the SDD whole-branch review
and resolve its findings. Then use the repository's PR workflow:

1. Push `feat/my-pages-default-host` to `origin`.
2. Open a GitHub PR targeting `master` with the verified test/build evidence.
3. Confirm required checks pass and merge the PR; do not push the feature
   branch directly to `master`.
4. Fetch the merged `origin/master` and verify the PR merge commit contains
   `GitLab.alfredworkflow` version `4.2.0`.
5. Confirm `refs/tags/v4.2.0` and the GitHub Release do not already exist.
6. Create annotated tag `v4.2.0` on the merged `origin/master` commit and push
   that tag.
7. Create a non-draft, non-prerelease GitHub Release with explicit notes
   summarizing `glhostdefault`, default-first `gl my`, non-default host
   drill-down, and GitLab-specific page mappings. Attach the reviewed
   `GitLab.alfredworkflow` artifact from the merged commit.
8. Verify the published release points at `v4.2.0` and lists
   `GitLab.alfredworkflow` as an asset.
