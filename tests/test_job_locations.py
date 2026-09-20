from job.locations import location_match

SETTINGS = {"countries": ["US", "JP"], "include_remote": False}
SETTINGS_REMOTE = {"countries": ["US", "JP"], "include_remote": True}


def test_location_match_us_city_state():
    assert location_match("San Francisco, CA", SETTINGS) == "match"


def test_location_match_japan():
    assert location_match("Tokyo, Japan", SETTINGS) == "match"


def test_location_match_outside_when_only_other_country():
    assert location_match("London, United Kingdom", SETTINGS) == "outside"


def test_location_match_unclear_with_no_recognizable_location():
    assert location_match("", SETTINGS) == "unclear"
    assert location_match("Earth", SETTINGS) == "unclear"


def test_location_match_remote_outside_when_remote_switch_off():
    assert location_match("Remote", SETTINGS) == "outside"


def test_location_match_remote_matches_when_remote_switch_on():
    assert location_match("Remote", SETTINGS_REMOTE) == "match"


def test_location_match_us_state_abbreviation_without_country_name():
    assert location_match("Austin, TX", SETTINGS) == "match"
