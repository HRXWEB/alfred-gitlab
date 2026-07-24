# Alfred Gitlab

Quickly navigate to GitLab projects in [Alfred 3][alfred].

![][sample]

## Setup and Usage
* Generate a GitLab personal access token (https://gitlab.com/profile/personal_access_tokens) then run `glsetkey <yourkey>`
* (Optionally) Tell it where the GitLab API you want to connect to is by running `glseturl https://<host>/api/v4/projects`
  * Defaults to GitLab.com's public API
* search for projects with `gl <search>`
* refresh projects immediately with `glrefresh`

### Upgrading from v3.0.1

The upstream v3.0.1 workflow cannot discover releases from this fork. Download
`GitLab.alfredworkflow` from this fork's [releases] page and install it once.
After v3.1.0 is installed, future update checks use `HRXWEB/alfred-gitlab`.

### Sub-Page Navigation
![][sub-page]
After selecting a repository, you are prompted with a page to navigate to. You can disable this behaviour
by setting the `quick_open` workflow variable to `true`.

See the Alfred documentation on [Workflow variables][wf-vars] for more information on how to configure workflow variables.

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
[wf-vars]: https://www.alfredapp.com/help/workflows/advanced/variables/
[license]: src/LICENSE.txt
[releases]: https://github.com/HRXWEB/alfred-gitlab/releases
[sample]: https://raw.github.com/lukewaite/alfred-gitlab/master/docs/sample.png
[sub-page]: https://raw.github.com/lukewaite/alfred-gitlab/master/docs/sub-page.png
