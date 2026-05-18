import pytest
from patch_bot.core.versions import max_version


def test_picks_highest_semver():
    assert max_version(["1.13.5", "1.15.1", "1.15.2", "1.15.1"]) == "1.15.2"


def test_single_version():
    assert max_version(["2.3.4"]) == "2.3.4"


def test_orders_minor_and_patch_correctly():
    # naive string sort would put 0.1.9 above 0.1.13
    assert max_version(["0.1.10", "0.1.13", "0.1.9"]) == "0.1.13"


def test_empty_raises():
    with pytest.raises(ValueError):
        max_version([])


def test_filters_falsy_entries():
    assert max_version(["", "1.0.0", ""]) == "1.0.0"


def test_unparseable_falls_back_to_string_sort():
    out = max_version(["not-a-version", "also-bad"])
    assert out in ("not-a-version", "also-bad")
