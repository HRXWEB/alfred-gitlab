# Alfred GitLab

Quickly navigate to projects across one or more GitLab hosts from [Alfred][alfred].

![][sample]

## Setup and usage

Generate a GitLab personal access token, then add each host with:

```text
glhostadd [name] <api_url> <token>
```

The name is optional. For example, these commands automatically use
`gitlab.example.test` and `192.0.2.10:8080` as their profile names:

```text
glhostadd https://gitlab.example.test/api/v4/projects example-token
glhostadd http://192.0.2.10:8080/api/v4/projects example-token
```

Bracketed IPv6 API URLs are also supported. Use a custom name when the
automatically derived host name would be inconvenient:

```text
glhostadd lab https://gitlab.example.test/api/v4/projects example-token
```

All examples are synthetic. Replace the URLs and token with values for your
GitLab installation.

The available commands are:

```text
glhostadd [name] <api_url> <token>
glhostdefault
glhostlist
glhostremove <name>
glrefresh
gl my
gl my <host-name> <page-filter>
gl <query>
```

`glhostdefault` opens a chooser for the default GitLab profile. `glhostlist`
shows configured profiles, cached project counts, and sanitized refresh status.
`glhostremove` removes the exact named profile and its scoped credential/cache.
`glrefresh` refreshes all profiles immediately and reports how many succeeded
or failed.

`gl <query>` searches the aggregate project cache across every configured
host. Result subtitles include the source profile name, and project IDs from
different hosts remain distinct in Alfred. Each host refreshes automatically
when its cache is older than one hour. If one host fails, other hosts still
refresh and the failing host keeps its previous cache, so a partial outage
does not hide its last known projects.

> [!WARNING]
> HTTPS is strongly recommended. HTTP API URLs send the GitLab token without
> transport encryption and produce a workflow warning. Alfred's Debugger also
> exposes Run Script command input, including the token supplied to
> `glhostadd`; do not add a host while the Debugger is recording or share its
> output.

### Compatibility and upgrades

The first v4 launch automatically migrates v3.1.0's URL, Keychain credential,
and project cache into the default host profile. Migration copies rather than
deletes the legacy settings, credential, and cache, preserving them for a
downgrade to v3.1.0.

The existing `glseturl <api_url>` and `glsetkey <token>` commands remain
compatible. They update the default profile (or create it when necessary),
while the `glhost*` commands manage additional profiles.

`glhostdefault` changes the host used by these compatibility commands.

### Personal GitLab pages

`gl my` opens the default host's personal-page menu first. It has nine rows:

| Page | GitLab path |
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

For another configured host, enter `gl my <host-name> <page-filter>`. Selecting
a non-default host drills into the same menu for that host; use a page filter
after its name to narrow the choices. The profile row resolves the signed-in
username from GitLab, while the remaining mappings use GitLab's host-specific
page paths above.

### Upgrading from v3.0.1

The upstream v3.0.1 workflow cannot discover releases from this fork. Download
`GitLab.alfredworkflow` from this fork's [releases] page and install it once.
After v3.1.0 is installed, future update checks use `HRXWEB/alfred-gitlab`.

### Sub-page navigation

![][sub-page]
After selecting a repository, you are prompted with a page to navigate to.
Enable the `Quick open` workflow configuration checkbox to skip this menu and
open repositories directly.

This workflow has been tested with Alfred 5.7.3 [2320].

## Notes

By default, we will only show projects which you are a member of.

## TODOs

* Optionally, allow you to search for non-membership repos
* Add alfred-workflow updater notifications
* Clean up

## Building

Build the installable workflow at `GitLab.alfredworkflow`:

```bash
./scripts/build.sh
```

The archive is built from the staged Git index. Stage source changes before
building; unstaged files are not included.

Pass a path to write the archive elsewhere:

```bash
./scripts/build.sh /tmp/GitLab.alfredworkflow
```

# Thanks, License, Copyright

- The [Alfred-Workflow][alfred-workflow] library is used heavily, and it's wonderful documentation was key in building the plugin.
- The GitLab icon is used, care of GitLab.

All other code/media are released under the [MIT Licence][license].

[alfred]: http://www.alfredapp.com/
[alfred-workflow]: http://www.deanishe.net/alfred-workflow/
[license]: src/LICENSE.txt
[releases]: https://github.com/HRXWEB/alfred-gitlab/releases
[sample]: https://raw.github.com/lukewaite/alfred-gitlab/master/docs/sample.png
[sub-page]: https://raw.github.com/lukewaite/alfred-gitlab/master/docs/sub-page.png
