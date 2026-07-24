import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import host_values
import hosts
from workflow import PasswordNotFound


class FakeWorkflow:
    def __init__(self):
        self.settings = {}
        self.passwords = {}
        self.saved_passwords = []
        self.deleted_passwords = []

    def save_password(self, account, password):
        self.passwords[account] = password
        self.saved_passwords.append((account, password))

    def get_password(self, account):
        try:
            return self.passwords[account]
        except KeyError:
            raise PasswordNotFound() from None

    def delete_password(self, account):
        del self.passwords[account]
        self.deleted_passwords.append(account)


class FailingSettings(dict):
    def update(self, values):
        raise OSError("settings unavailable")


class PartiallyFailingSettings(dict):
    def update(self, values):
        super().update(values)
        raise OSError("settings unavailable")


class FakeCache:
    def __init__(self):
        self.legacy_projects = None
        self.project_values = {}
        self.invalidated = []
        self.cleared = []

    def load_legacy_projects(self):
        return self.legacy_projects

    def migrate_legacy_projects(self, profile_id, projects):
        self.project_values[profile_id] = projects
        return True

    def projects(self, profile_id):
        return self.project_values.get(profile_id)

    def invalidate_projects(self, profile_id):
        self.invalidated.append(profile_id)

    def clear_profile_state(self, profile_id):
        self.project_values.pop(profile_id, None)
        self.cleared.append(profile_id)


@pytest.mark.parametrize(
    ("legacy_url", "has_token", "expected_name"),
    [
        (
            "https://gitlab.example.com/api/v4/projects",
            True,
            "gitlab.example.com",
        ),
        (None, True, "gitlab.com"),
        (
            "http://192.0.2.10:8081/api/v4/projects",
            False,
            "192.0.2.10:8081",
        ),
    ],
)
def test_legacy_migration_when_legacy_state_exists_is_idempotent(
    legacy_url,
    has_token,
    expected_name,
):
    # Given: a v3.1 workflow with legacy settings, credential, and cache
    workflow = FakeWorkflow()
    cache = FakeCache()
    if legacy_url is not None:
        workflow.settings["api_url"] = legacy_url
    if has_token:
        workflow.passwords["gitlab_api_key"] = "example-token"
    cache.legacy_projects = [{"id": 7}]

    # When: profiles are ensured repeatedly
    first = hosts.ensure_profiles(workflow, cache)
    second = hosts.ensure_profiles(workflow, cache)

    # Then: one stable profile is migrated without removing legacy state
    assert len(first) == 1
    assert second == first
    assert first[0]["name"] == expected_name
    assert cache.projects(first[0]["id"]) == [{"id": 7}]
    assert workflow.settings.get("api_url") == legacy_url
    assert cache.legacy_projects == [{"id": 7}]
    if has_token:
        assert workflow.passwords["gitlab_api_key"] == "example-token"
        assert workflow.saved_passwords == [
            (
                hosts.token_account(first[0]["id"]),
                "example-token",
            )
        ]


def test_legacy_migration_when_settings_fail_rolls_back_new_state():
    # Given: legacy state whose settings store cannot commit the registry
    workflow = FakeWorkflow()
    workflow.settings = FailingSettings(
        {"api_url": "https://gitlab.example.com/api/v4/projects"}
    )
    workflow.passwords["gitlab_api_key"] = "example-token"
    cache = FakeCache()
    cache.legacy_projects = [{"id": 7}]

    # When: migration reaches the final settings commit
    with pytest.raises(OSError):
        hosts.ensure_profiles(workflow, cache)

    # Then: no profile registry, scoped credential, or scoped cache remains
    assert "hosts" not in workflow.settings
    assert set(workflow.passwords) == {"gitlab_api_key"}
    assert cache.project_values == {}
    assert len(cache.cleared) == 1


def test_legacy_migration_when_settings_partially_commit_removes_registry():
    # Given: a settings store that mutates before reporting failure
    workflow = FakeWorkflow()
    workflow.settings = PartiallyFailingSettings(
        {"api_url": "https://gitlab.example.com/api/v4/projects"}
    )
    workflow.passwords["gitlab_api_key"] = "example-token"
    cache = FakeCache()

    # When: migration cannot finish the settings commit
    with pytest.raises(OSError):
        hosts.ensure_profiles(workflow, cache)

    # Then: all new registry keys and scoped credentials are rolled back
    assert set(workflow.settings) == {"api_url"}
    assert set(workflow.passwords) == {"gitlab_api_key"}


def test_legacy_migration_when_keychain_partially_writes_rolls_back_token():
    # Given: a Keychain write that mutates before reporting failure
    workflow = FakeWorkflow()
    workflow.passwords["gitlab_api_key"] = "example-token"
    cache = FakeCache()
    original_save_password = workflow.save_password

    def save_then_fail(account, password):
        original_save_password(account, password)
        raise OSError("keychain unavailable")

    workflow.save_password = save_then_fail

    # When: migration cannot finish its scoped credential write
    with pytest.raises(OSError):
        hosts.ensure_profiles(workflow, cache)

    # Then: no registry or scoped credential remains
    assert "hosts" not in workflow.settings
    assert set(workflow.passwords) == {"gitlab_api_key"}


def test_legacy_migration_when_cache_fails_rolls_back_scoped_credential():
    # Given: legacy state whose new profile cache cannot be written
    workflow = FakeWorkflow()
    workflow.passwords["gitlab_api_key"] = "example-token"
    cache = FakeCache()
    cache.legacy_projects = [{"id": 7}]
    cache.migrate_legacy_projects = lambda profile_id, projects: (
        (_ for _ in ()).throw(OSError("cache unavailable"))
    )

    # When: migration attempts to copy the cache
    with pytest.raises(OSError):
        hosts.ensure_profiles(workflow, cache)

    # Then: neither registry nor scoped credential is retained
    assert "hosts" not in workflow.settings
    assert set(workflow.passwords) == {"gitlab_api_key"}


def test_set_default_token_when_no_profile_creates_gitlab_com_default():
    # Given: a workflow with no legacy or v4 profile state
    workflow = FakeWorkflow()
    cache = FakeCache()

    # When: the compatibility key command stores a token
    profile = hosts.set_default_token(workflow, "example-token", cache)

    # Then: a GitLab.com default profile is created and only it is invalidated
    assert profile["name"] == "gitlab.com"
    assert workflow.passwords[hosts.token_account(profile["id"])] == (
        "example-token"
    )
    assert cache.invalidated == [profile["id"]]


def test_set_default_url_when_name_was_custom_preserves_name():
    # Given: a default profile with a user-selected name
    workflow = FakeWorkflow()
    cache = FakeCache()
    registry = hosts.HostRegistry(workflow)
    profile = hosts.add_or_update_profile(
        registry,
        host_values.ProfileDraft(
            name="company",
            api_url="https://old.example.com/api/v4/projects",
            token="example-token",
        ),
    )

    # When: the compatibility URL command changes its API URL
    updated = hosts.set_default_url(
        workflow,
        "http://new.example.com/api/v4/projects",
        cache,
    )

    # Then: the custom name and identity remain and only its cache is invalidated
    assert updated["id"] == profile["id"]
    assert updated["name"] == "company"
    assert cache.invalidated == [profile["id"]]


def test_set_default_url_when_name_was_derived_updates_name():
    # Given: a default profile using its auto-derived name
    workflow = FakeWorkflow()
    cache = FakeCache()
    registry = hosts.HostRegistry(workflow)
    profile = hosts.add_or_update_profile(
        registry,
        host_values.ProfileDraft(
            name=None,
            api_url="https://old.example.com/api/v4/projects",
            token="example-token",
        ),
    )

    # When: the compatibility URL command changes its API URL
    updated = hosts.set_default_url(
        workflow,
        "https://new.example.com/api/v4/projects",
        cache,
    )

    # Then: the auto-derived name follows the new URL
    assert updated["id"] == profile["id"]
    assert updated["name"] == "new.example.com"


def test_remove_profile_when_cache_cleanup_fails_retains_credential():
    # Given: a stored profile whose cache cleanup fails
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(
        workflow,
        hosts.RegistryCallbacks(
            cleanup=lambda wf, profile_id: (_ for _ in ()).throw(
                OSError("cache unavailable")
            )
        ),
    )
    profile = hosts.add_or_update_profile(
        registry,
        host_values.ProfileDraft(
            name="company",
            api_url="https://gitlab.example.com/api/v4/projects",
            token="example-token",
        ),
    )

    # When: removal cannot clean the scoped cache
    with pytest.raises(OSError):
        hosts.remove_profile(registry, "company")

    # Then: the stored profile still has its credential
    assert hosts.get_profiles(workflow) == [profile]
    assert workflow.passwords[hosts.token_account(profile["id"])] == (
        "example-token"
    )


def test_remove_profile_when_settings_fail_retains_credential():
    # Given: a stored profile whose settings cannot be updated
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    profile = hosts.add_or_update_profile(
        registry,
        host_values.ProfileDraft(
            name="company",
            api_url="https://gitlab.example.com/api/v4/projects",
            token="example-token",
        ),
    )
    workflow.settings = FailingSettings(workflow.settings)

    # When: removal cannot commit the reduced registry
    with pytest.raises(OSError):
        hosts.remove_profile(registry, "company")

    # Then: the stored profile still has its credential
    assert hosts.get_profiles(workflow) == [profile]
    assert workflow.passwords[hosts.token_account(profile["id"])] == (
        "example-token"
    )


def test_remove_profile_when_keychain_delete_fails_restores_registry():
    # Given: a stored profile whose credential cannot be deleted
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    profile = hosts.add_or_update_profile(
        registry,
        host_values.ProfileDraft(
            name="company",
            api_url="https://gitlab.example.com/api/v4/projects",
            token="example-token",
        ),
    )
    def delete_then_fail(account):
        del workflow.passwords[account]
        raise OSError("keychain unavailable")

    workflow.delete_password = delete_then_fail

    # When: removal reaches the Keychain delete
    with pytest.raises(OSError):
        hosts.remove_profile(registry, "company")

    # Then: the registry is restored with its credential intact
    assert hosts.get_profiles(workflow) == [profile]
    assert workflow.passwords[hosts.token_account(profile["id"])] == (
        "example-token"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitlab.example.com/api/v4/projects", "gitlab.example.com"),
        ("http://192.0.2.10:8081/api/v4/projects", "192.0.2.10:8081"),
        ("http://[2001:db8::10]:8081/api/v4/projects", "[2001:db8::10]:8081"),
    ],
)
def test_derive_host_name_when_url_is_valid(url, expected):
    # Given: a valid GitLab API URL
    # When: its default profile name is derived
    result = host_values.derive_host_name(url)

    # Then: the authority is used without a scheme or path
    assert result == expected


def test_parse_host_add_when_alias_is_omitted():
    # Given: an add command containing an API URL and token
    argument = "https://gitlab.example.com/api/v4/projects example-token"

    # When: the command is parsed
    result = host_values.parse_host_add(argument)

    # Then: no explicit alias is returned
    assert result == (
        None,
        "https://gitlab.example.com/api/v4/projects",
        "example-token",
    )


def test_parse_host_add_when_alias_is_provided():
    # Given: an add command containing an alias, API URL, and token
    argument = (
        "company https://gitlab.example.com/api/v4/projects example-token"
    )

    # When: the command is parsed
    result = host_values.parse_host_add(argument)

    # Then: the explicit alias is returned
    assert result == (
        "company",
        "https://gitlab.example.com/api/v4/projects",
        "example-token",
    )


def test_add_profile_when_alias_is_omitted_uses_derived_name_and_keychain():
    # Given: an empty registry and profile-scoped invalidation callback
    workflow = FakeWorkflow()
    invalidated = []
    registry = hosts.HostRegistry(
        workflow,
        hosts.RegistryCallbacks(
            invalidate=lambda wf, profile_id: invalidated.append(profile_id)
        ),
    )
    draft = hosts.ProfileDraft(
        name=None,
        api_url="http://192.0.2.10:8081/api/v4/projects",
        token="example-token",
    )

    # When: the profile is added without an alias
    profile = hosts.add_or_update_profile(registry, draft)

    # Then: its derived profile and scoped token are persisted
    assert profile["name"] == "192.0.2.10:8081"
    assert profile["id"]
    assert "token" not in profile
    assert workflow.saved_passwords == [
        (f"gitlab_api_key:{profile['id']}", "example-token")
    ]
    assert invalidated == [profile["id"]]


def test_update_profile_when_name_matches_preserves_id_and_invalidates_it():
    # Given: an existing named profile
    workflow = FakeWorkflow()
    invalidated = []
    registry = hosts.HostRegistry(
        workflow,
        hosts.RegistryCallbacks(
            invalidate=lambda wf, profile_id: invalidated.append(profile_id)
        ),
    )
    first = hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="company",
            api_url="https://old.example.com/api/v4/projects",
            token="old-token",
        ),
    )
    invalidated.clear()

    # When: the same name is registered with replacement values
    second = hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="company",
            api_url="https://new.example.com/api/v4/projects",
            token="replacement-token",
        ),
    )

    # Then: the identity is retained and only that profile is invalidated
    assert second["id"] == first["id"]
    assert invalidated == [first["id"]]


@pytest.mark.parametrize(
    ("name", "api_url"),
    [
        ("company", "https://bad_host/api/v4/projects"),
        ("", "https://gitlab.example.com/api/v4/projects"),
        ("bad name", "https://gitlab.example.com/api/v4/projects"),
    ],
)
def test_add_profile_when_input_is_invalid_does_not_mutate_storage(
    name, api_url
):
    # Given: an empty host registry
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    draft = hosts.ProfileDraft(
        name=name,
        api_url=api_url,
        token="example-token",
    )

    # When: invalid profile input is registered
    with pytest.raises(ValueError):
        hosts.add_or_update_profile(registry, draft)

    # Then: neither settings nor Keychain are changed
    assert workflow.settings == {}
    assert workflow.saved_passwords == []


def test_update_profile_when_settings_fail_restores_previous_token():
    # Given: an existing profile whose settings store later becomes unavailable
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    first = hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="company",
            api_url="https://old.example.com/api/v4/projects",
            token="old-token",
        ),
    )
    workflow.settings = FailingSettings(workflow.settings)

    # When: replacement profile settings cannot be saved
    with pytest.raises(OSError):
        hosts.add_or_update_profile(
            registry,
            hosts.ProfileDraft(
                name="company",
                api_url="https://new.example.com/api/v4/projects",
                token="replacement-token",
            ),
        )

    # Then: the original Keychain token is restored
    account = hosts.token_account(first["id"])
    assert workflow.passwords[account] == "old-token"
    assert workflow.saved_passwords[-2:] == [
        (account, "replacement-token"),
        (account, "old-token"),
    ]


def test_get_default_profile_when_default_is_missing_uses_first_profile():
    # Given: two profiles without a matching configured default
    workflow = FakeWorkflow()
    workflow.settings = {
        "hosts": [
            {
                "id": "first-id",
                "name": "first",
                "api_url": "https://first.example.com/api/v4/projects",
            },
            {
                "id": "second-id",
                "name": "second",
                "api_url": "https://second.example.com/api/v4/projects",
            },
        ],
        "default_host_id": "missing-id",
    }

    # When: the default profile is loaded
    profile = hosts.get_default_profile(workflow)

    # Then: the first stored profile is returned
    assert profile is not None
    assert profile["id"] == "first-id"


def test_remove_profile_when_removing_default_selects_first_remaining():
    # Given: two profiles with the first selected by default
    workflow = FakeWorkflow()
    cleaned = []
    registry = hosts.HostRegistry(
        workflow,
        hosts.RegistryCallbacks(
            cleanup=lambda wf, profile_id: cleaned.append(profile_id)
        ),
    )
    first = hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="first",
            api_url="https://first.example.com/api/v4/projects",
            token="first-token",
        ),
    )
    second = hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="second",
            api_url="https://second.example.com/api/v4/projects",
            token="second-token",
        ),
    )

    # When: the default profile is removed by exact name
    removed = hosts.remove_profile(registry, "first")

    # Then: its token and cache are removed and the remaining profile is default
    assert removed == first
    assert workflow.deleted_passwords == [hosts.token_account(first["id"])]
    assert cleaned == [first["id"]]
    assert workflow.settings["default_host_id"] == second["id"]
    assert hosts.get_profiles(workflow) == [second]


def test_remove_profile_when_name_is_unknown_does_not_mutate_storage():
    # Given: a registry containing one profile
    workflow = FakeWorkflow()
    registry = hosts.HostRegistry(workflow)
    hosts.add_or_update_profile(
        registry,
        hosts.ProfileDraft(
            name="company",
            api_url="https://gitlab.example.com/api/v4/projects",
            token="example-token",
        ),
    )
    original_settings = dict(workflow.settings)

    # When: an unknown exact name is removed
    with pytest.raises(ValueError):
        hosts.remove_profile(registry, "missing")

    # Then: settings and Keychain are unchanged
    assert workflow.settings == original_settings
    assert workflow.deleted_passwords == []
