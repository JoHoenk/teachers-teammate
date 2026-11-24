"""Which release is offered: the version rules and the page the banner may open."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.releases import ReleaseOffer, newer_release, version_tuple

_RELEASE_PAGE = "https://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0"


# ── version_tuple ─────────────────────────────────────────────────────────────


def test_version_tuple_strips_v_prefix() -> None:
    """
    Given  a version tag with a 'v' prefix
    When   version_tuple() is called
    Then   the prefix is stripped, each part is an int, and the final-release flag is 0
    """
    assert version_tuple("v1.2.3") == (1, 2, 3, 0)


def test_version_tuple_strips_prerelease_suffix() -> None:
    """
    Given  a version tag with a pre-release suffix separated by '-'
    When   version_tuple() is called
    Then   the release part is returned with the pre-release flag -1
    """
    assert version_tuple("1.2.3-rc1") == (1, 2, 3, -1)


def test_version_tuple_reads_a_pep440_prerelease() -> None:
    """
    Given  a version as the app writes it, with the label right after the release part
    When   version_tuple() is called
    Then   it is read as a pre-release of that release
    """
    assert version_tuple("0.5.7rc1") == (0, 5, 7, -1)


def test_version_tuple_reads_a_tag_with_a_dotted_prerelease() -> None:
    """
    Given  a release tag whose pre-release number follows a dot
    When   version_tuple() is called
    Then   it is read as a pre-release of that release
    """
    assert version_tuple("v0.5.7-rc.1") == (0, 5, 7, -1)


def test_a_prerelease_sorts_below_its_release() -> None:
    """
    Given  the pre-release and the final release of the same version
    When   both are converted
    Then   the pre-release compares lower, so a final release is offered to an rc tester
    """
    assert version_tuple("0.5.7rc1") < version_tuple("v0.5.7")


def test_version_tuple_bare_version() -> None:
    """
    Given  a plain version string with no prefix or suffix
    When   version_tuple() is called
    Then   the parts are returned as ints, trailing zeros of the release dropped
    """
    assert version_tuple("2.0.0") == (2, 0)


def test_version_tuple_treats_trailing_zeros_as_the_same_version() -> None:
    """
    Given  '0.6' and the tag 'v0.6.0'
    When   both are converted
    Then   they are equal, so the tag is not offered as an update
    """
    assert version_tuple("0.6") == version_tuple("v0.6.0")


@pytest.mark.parametrize("raw", ["0.5.7.dev0", "0.5.7.post1", "0.5.7+abc", "0.5.7rc1.dev2"])
def test_version_tuple_reads_dev_post_and_local_versions(raw: str) -> None:
    """
    Given  a development, post or local version of a release
    When   version_tuple() is called
    Then   it compares like that release (rc ones like their pre-release), never as unreadable
    """
    expected = version_tuple("0.5.7rc1") if "rc1" in raw else version_tuple("0.5.7")
    assert version_tuple(raw) == expected


@pytest.mark.parametrize("raw", ["v0.5.7-rc.", "v0.5.7-", "v0.5.7-rcx"])
def test_version_tuple_rejects_a_label_without_a_whole_number(raw: str) -> None:
    """
    Given  a tag with a dangling dot or an unknown label
    When   version_tuple() is called
    Then   an empty tuple is returned
    """
    assert version_tuple(raw) == ()


def test_version_tuple_invalid_returns_empty() -> None:
    """
    Given  a string that contains no numeric version segments
    When   version_tuple() is called
    Then   an empty tuple is returned
    """
    assert version_tuple("not-a-version") == ()


def test_version_tuple_empty_string_returns_empty() -> None:
    """
    Given  an empty string
    When   version_tuple() is called
    Then   an empty tuple is returned
    """
    assert version_tuple("") == ()


# ── newer_release ─────────────────────────────────────────────────────────────


def test_a_newer_release_is_offered() -> None:
    """
    Given  the installed version is 0.9.0 and the latest release is v1.0.0
    When   the offer is decided
    Then   its version without the tag prefix and its URL come back
    """
    assert newer_release("v1.0.0", _RELEASE_PAGE, "0.9.0") == ReleaseOffer(
        version="1.0.0", url=_RELEASE_PAGE
    )


def test_the_same_version_is_not_offered() -> None:
    """
    Given  the installed version equals the latest release (both 1.0.0)
    When   the offer is decided
    Then   nothing is offered
    """
    assert newer_release("v1.0.0", _RELEASE_PAGE, "1.0.0") is None


def test_an_older_release_is_not_offered() -> None:
    """
    Given  the installed version (1.1.0) is newer than the latest release (v1.0.0)
    When   the offer is decided
    Then   nothing is offered
    """
    assert newer_release("v1.0.0", _RELEASE_PAGE, "1.1.0") is None


def test_an_rc_build_is_not_offered_the_previous_release() -> None:
    """
    Given  the installed version is the pre-release 0.5.7rc1 and the latest release is v0.5.6
    When   the offer is decided
    Then   nothing is offered: the rc is newer than that release
    """
    assert newer_release("v0.5.6", _RELEASE_PAGE, "0.5.7rc1") is None


def test_an_rc_build_is_offered_the_final_release() -> None:
    """
    Given  the installed version is the pre-release 0.5.7rc1 and the latest release is v0.5.7
    When   the offer is decided
    Then   it is offered with its version 0.5.7
    """
    offered = newer_release("v0.5.7", _RELEASE_PAGE, "0.5.7rc1")

    assert offered is not None
    assert offered.version == "0.5.7"


def test_a_final_build_is_offered_the_next_release() -> None:
    """
    Given  the installed version is 0.5.6 and the latest release is v0.5.7
    When   the offer is decided
    Then   it is offered with its version 0.5.7
    """
    offered = newer_release("v0.5.7", _RELEASE_PAGE, "0.5.6")

    assert offered is not None
    assert offered.version == "0.5.7"


def test_a_tag_that_is_not_a_version_is_not_offered() -> None:
    """
    Given  the latest release carries a tag that is not a version
    When   the offer is decided
    Then   nothing is offered
    """
    assert newer_release("nightly", _RELEASE_PAGE, "0.5.6") is None


@pytest.mark.parametrize("installed", ["0.5.7.dev0", "0.5.7+abc", "0.5.7.post1", "garbage"])
def test_a_build_of_the_latest_release_is_not_offered_it(installed: str) -> None:
    """
    Given  an installed version that is a dev, post or local build of v0.5.7, or unreadable
    When   v0.5.7 is the latest release
    Then   nothing is offered
    """
    assert newer_release("v0.5.7", _RELEASE_PAGE, installed) is None


def test_a_development_build_is_not_offered_a_release() -> None:
    """
    Given  a development build (0.0.0.dev0) and a newer release v1.0.0
    When   the offer is decided
    Then   nothing is offered: the placeholder sorts below every release
    """
    assert newer_release("v1.0.0", _RELEASE_PAGE, "0.0.0.dev0") is None


def test_a_released_dev_version_is_offered_a_newer_release() -> None:
    """
    Given  a stamped release whose version carries a .dev suffix (0.6.0.dev1) and a newer v0.7.0
    When   the offer is decided
    Then   it is offered: only the unstamped placeholder is a development build
    """
    offered = newer_release("v0.7.0", _RELEASE_PAGE, "0.6.0.dev1")

    assert offered is not None
    assert offered.version == "0.7.0"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/rel",
        "https://github.com/someone-else/teachers-teammate/releases/tag/v1.0.0",
        "http://github.com/JoHoenk/teachers-teammate/releases/tag/v1.0.0",
        "https://github.com/JoHoenk/teachers-teammate/",
    ],
)
def test_a_release_page_that_is_not_this_projects_is_not_offered(url: str) -> None:
    """
    Given  the feed names a newer version but points outside this project's releases
    When   the offer is decided
    Then   nothing is offered: the banner opens only a page of this repository
    """
    assert newer_release("v1.0.0", url, "0.9.0") is None
