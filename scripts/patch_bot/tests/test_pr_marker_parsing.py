from patch_bot.github.prs import (
    PullRequest,
    find_dependabot_prs_for_group,
    find_our_pr_for_group,
)


def _pr(*, number, body, labels=(), user="patch-bot"):
    return PullRequest(
        number=number,
        title="",
        body=body,
        head_ref="",
        base_ref="main",
        labels=list(labels),
        user_login=user,
        state="open",
    )


def test_ghsa_ids_in_body_collects_all_markers():
    pr = _pr(
        number=1,
        body=(
            "<!-- patchbot:ghsa=GHSA-aaaa-1111-bbbb -->\n"
            "<!-- patchbot:ghsa=GHSA-cccc-2222-dddd -->"
        ),
    )
    assert pr.ghsa_ids_in_body == {"GHSA-aaaa-1111-bbbb", "GHSA-cccc-2222-dddd"}


def test_finds_our_pr_when_group_overlaps_markers():
    prs = [
        _pr(
            number=1,
            body="<!-- patchbot:ghsa=GHSA-aaaa-1111-bbbb -->",
            labels=["patch-bot/security"],
        ),
    ]
    assert find_our_pr_for_group(prs, {"GHSA-zzzz-9999-yyyy", "GHSA-aaaa-1111-bbbb"}).number == 1
    assert find_our_pr_for_group(prs, {"GHSA-zzzz-9999-yyyy"}) is None


def test_our_pr_must_be_labelled():
    prs = [_pr(number=1, body="<!-- patchbot:ghsa=GHSA-x -->", labels=[])]
    assert find_our_pr_for_group(prs, {"GHSA-x"}) is None


def test_finds_all_dependabot_prs_for_group():
    prs = [
        _pr(number=10, body="Bumps axios\nGHSA-aaaa-1111-bbbb", user="dependabot[bot]"),
        _pr(number=11, body="Bumps axios\nGHSA-cccc-2222-dddd", user="dependabot[bot]"),
        _pr(number=12, body="GHSA-eeee-3333-ffff", user="dependabot[bot]"),
    ]
    found = find_dependabot_prs_for_group(prs, {"GHSA-aaaa-1111-bbbb", "GHSA-cccc-2222-dddd"})
    assert sorted(p.number for p in found) == [10, 11]


def test_dependabot_finder_ignores_non_dependabot_authors():
    prs = [_pr(number=10, body="GHSA-aaaa-1111-bbbb", user="alice")]
    assert find_dependabot_prs_for_group(prs, {"GHSA-aaaa-1111-bbbb"}) == []
