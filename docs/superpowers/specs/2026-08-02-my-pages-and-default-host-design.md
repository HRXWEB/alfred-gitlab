# Personal Pages and Default Host Design

## Goal

Add two related navigation features to the Alfred GitLab workflow:

- `glhostdefault` lets the user select the default GitLab host.
- `gl my` opens personal GitLab pages, prioritizing the default host while
  keeping other configured hosts available through a second-level menu.

The feature must preserve the existing aggregate project search behavior and
work with GitLab.com, self-managed hosts, explicit ports, IP addresses, IPv6
addresses, and GitLab installations served below a URL subpath.

## User Experience

### Selecting the default host

Entering `glhostdefault` displays every configured host. The current default
host appears first and is labeled `Current default`. It is not actionable.
Every other row is actionable; selecting one stores that profile's stable ID as
`default_host_id` and displays a success notification.

Changing the default does not modify credentials, caches, refresh status, or
the order of the configured profiles. If no hosts are configured, the filter
shows the same setup guidance used by the existing workflow.

### Opening personal pages on the default host

Entering `gl my` displays these pages for the default host, in this order:

1. Profile
2. Starred projects
3. Snippets
4. Merge requests
5. Projects
6. Issues
7. Preferences
8. Dashboard
9. To-do list

Each row identifies the default host in its subtitle and opens the corresponding
page directly. The GitLab names deliberately replace GitHub terminology:
snippets correspond to gists, merge requests correspond to pull requests, and
the to-do list is the closest GitLab equivalent to notifications.

After the nine page rows, the filter displays one non-actionable navigation row
for every non-default host. Selecting such a row autocompletes the Alfred query
to `my <host-name> ` and displays the same nine pages for that host.

The page portion remains filterable. For example, `gl my company issues`
selects the `company` profile and filters its page menu to Issues. Host names
cannot contain whitespace under the existing host validation rules, so the
query grammar remains unambiguous.

When only one host is configured, `gl my` displays only its page rows.

## Architecture

The existing `gl` Script Filter remains the single entry point. `gitlab.py`
recognizes queries beginning with the complete token `my` and delegates them to
a focused personal-pages module. Other input continues through aggregate
project search unchanged; words merely beginning with `my` remain ordinary
project queries.

The personal-pages module owns:

- parsing the selected host and optional page filter;
- ordering the default and non-default host rows;
- deriving web and API roots from a configured projects API URL;
- defining page metadata and paths;
- resolving and caching the authenticated username; and
- rendering actionable page rows and non-actionable host navigation rows.

Default-host persistence belongs to the host registry rather than the UI
layer. The registry exposes an operation that accepts a stable profile ID,
validates that it exists, and updates only `default_host_id`. A host-command
wrapper formats the user-facing result.

`info.plist` adds a `glhostdefault` Script Filter connected to a Run Script
action and a notification. The Script Filter passes profile IDs, not names, to
the action so later display-name changes cannot select the wrong profile.

## URL and Identity Handling

The configured API URL is expected to end in `/api/v4/projects`. Removing that
suffix produces both the GitLab web root and the API v4 root while preserving
the scheme, authority, explicit port, IPv6 syntax, and any deployment subpath.
If the configured URL does not have the expected suffix, the personal-pages
module treats it as unsupported rather than guessing a destination.

The menu uses these relative destinations:

| Page | Destination |
| --- | --- |
| Profile | `/<username>` |
| Starred projects | `/dashboard/projects/starred` |
| Snippets | `/dashboard/snippets` |
| Merge requests | `/dashboard/merge_requests` |
| Projects | `/dashboard/projects` |
| Issues | `/dashboard/issues` |
| Preferences | `/-/profile/preferences` |
| Dashboard | `/` |
| To-do list | `/dashboard/todos` |

Profile is the only destination that requires the authenticated username. The
module resolves it from `<api-v4-root>/user` using the selected profile's
existing Keychain token and caches only the username under a profile-scoped
key. The cache prevents repeated network calls while Alfred reruns the Script
Filter during typing. A bounded cache age allows a renamed GitLab username to
eventually refresh without adding a manual invalidation command.

No token, API response body, or internal exception detail appears in Alfred
feedback or logs added by this feature.

## Error Handling

- With no configured hosts, both new entry points show setup guidance and do
  not raise an exception.
- If a selected host name no longer exists, `gl my` returns to the default-host
  menu and shows a concise unavailable-host warning.
- If username resolution fails, the remaining eight personal pages remain
  available. Profile is shown as invalid with a sanitized retry-later message.
- If a page root cannot be derived from an invalid API path, page rows for that
  host are invalid and explain that its API URL is unsupported.
- Attempting to set an unknown profile ID fails without changing settings.
- A settings write failure while changing the default leaves the former
  default selected.

## Testing

Unit tests cover:

- selecting a known default profile and rejecting an unknown profile;
- preserving all settings except `default_host_id`;
- current-default ordering and actionability in `glhostdefault`;
- exact `my` token dispatch without intercepting ordinary project searches;
- default page ordering followed by non-default host rows;
- second-level host selection and page filtering;
- single-host and no-host behavior;
- URL derivation for domains, ports, IPv4, bracketed IPv6, and subpath installs;
- username retrieval, profile-scoped caching, and sanitized failure fallback;
- `info.plist` wiring for the new filter, action, and notification; and
- Python 3.9 compatibility.

The full automated test suite and workflow build run before release. The built
archive is inspected to confirm that new source files and the updated plist are
included.

## Documentation and Release

README and the workflow's embedded readme document `glhostdefault` and the
`gl my` interaction. CHANGELOG records the feature under `v4.2.0`, and
`info.plist` advances the workflow version from `4.1.0` to `4.2.0` with a new
integer build number.

After tests and build verification pass, the implementation is committed, the
`v4.2.0` tag is created, and a GitHub Release is published with
`GitLab.alfredworkflow` attached. Release notes summarize default-host
selection, default-first personal navigation, multi-host drill-down, and the
GitLab-specific page mappings.

Publishing is attempted only from the intended `HRXWEB/alfred-gitlab`
repository and only after confirming that the tag and release do not already
exist.
