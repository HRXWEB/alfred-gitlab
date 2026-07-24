import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import hosts


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
        return self.passwords[account]

    def delete_password(self, account):
        del self.passwords[account]
        self.deleted_passwords.append(account)


class FailingSettings(dict):
    def update(self, values):
        raise OSError("settings unavailable")


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
    result = hosts.derive_host_name(url)

    # Then: the authority is used without a scheme or path
    assert result == expected


def test_parse_host_add_when_alias_is_omitted():
    # Given: an add command containing an API URL and token
    argument = "https://gitlab.example.com/api/v4/projects example-token"

    # When: the command is parsed
    result = hosts.parse_host_add(argument)

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
    result = hosts.parse_host_add(argument)

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
