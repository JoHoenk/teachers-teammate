"""Unit tests for teachers_teammate.domain.model_listing."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_listing import ModelListing


@pytest.mark.parametrize(
    ("listing", "cacheable"),
    [
        (ModelListing(["a"]), True),
        (ModelListing(["a"], "builtin"), True),
        (ModelListing(["a"], "failed", "no key"), False),
        (ModelListing([]), False),
    ],
)
def test_only_a_non_empty_answer_that_did_not_fail_is_cacheable(
    listing: ModelListing, cacheable: bool
) -> None:
    """
    Given  a listing that is live, built-in, failed or empty
    When   cacheable is read
    Then   only a non-empty live or built-in one may be remembered, so a key entered or a
           server started a moment later is noticed on the next look
    """
    assert listing.cacheable is cacheable
