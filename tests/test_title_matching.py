"""Issue #68: fuzzy title matching, and a posting is only kept with a matched title."""
import copy

import pytest

from job import config
from job.matching import borderline_title, classify_posting, match_title, reconsider_rejection

TITLES_DOC = config.load_titles()
ACTIVE = config.active_titles(TITLES_DOC)
LOCATIONS = {"countries": ["US", "JP"], "include_remote": False}

# Henry is adding "MBA" to config/titles.json separately; the single-word rule
# is tested against a copy that has it.
MBA_DOC = copy.deepcopy(TITLES_DOC)
MBA_DOC["titles"].append({"title": "MBA", "group": "General", "aliases": [], "active": True})
MBA_ACTIVE = config.active_titles(MBA_DOC)


@pytest.mark.parametrize("title", [
    "Intern, Product Management (MBA)",
    "Product Mgmt Intern - Summer 2027",
    "Assoc Product Manager Intern",
    "Sr. Product Manager MBA Intern",
    "Product Manger Intern",  # one typo
    "PM Intern (Req 12345)",
])
def test_wording_variations_match_a_title(title):
    result = classify_posting(title, "Summer 2027", TITLES_DOC)
    assert result.decision == "match", (title, result)
    assert result.matched_title
    assert result.match_method in {"exact", "alias"} or result.match_method.startswith("fuzzy:")


def test_fuzzy_matches_record_their_score():
    match = match_title("Product Manger Intern", ACTIVE)
    assert match.method == "fuzzy" and match.score >= 90
    assert match.method_label == f"fuzzy:{match.score}"
    assert match_title("Product Manager Intern", ACTIVE).method == "exact"
    assert match_title("MBA Intern, Product Management - Seattle", ACTIVE).method in {"alias", "exact"}


@pytest.mark.parametrize("title", [
    "Product Marketing Intern",  # excluded word
    "Production Supervisor Intern",
    "Project Manager Intern",
    "Software Engineer Intern",
    "AMS Lab Validation Intern",
])
def test_other_roles_do_not_match(title):
    assert match_title(title, ACTIVE) is None
    assert classify_posting(title, "", TITLES_DOC).decision == "reject"


def test_ams_lab_validation_intern_is_rejected_by_fuzzy_matching_too():
    titles = [{"title": "Product Development Intern", "group": "Core PM", "aliases": [], "active": True}]
    assert match_title("Lab Validation Intern", titles) is None
    assert borderline_title("Lab Validation Intern", titles) is None
    doc = {"exclude_words": [], "titles": titles}
    assert classify_posting("AMS Lab Validation Intern", "", doc).decision == "reject"


def test_a_score_of_80_to_89_goes_to_classification_not_a_match():
    title = "Intern Product Manager Search Maps Cloud Seattle"
    assert match_title(title, ACTIVE) is None
    border = borderline_title(title, ACTIVE)
    assert border is not None and 80 <= border.score < 90
    result = classify_posting(title, "", TITLES_DOC)
    assert result.decision == "classify" and result.matched_title is None


def test_a_title_in_another_language_goes_to_classification():
    assert classify_posting("プロダクトマネージャー インターン", "", TITLES_DOC).decision == "classify"


@pytest.mark.parametrize("title", [
    "Hardware Product Manager Intern",
    "Product Manager Intern, Product Design",
    "Product Manager Intern - Sales",
    "Undergraduate Product Manager Intern",
    "Lab Product Manager Intern",
])
def test_clearly_off_role_titles_are_rejected_even_when_close(title):
    result = classify_posting(title, "Summer 2027", TITLES_DOC)
    assert result.decision == "reject"
    assert "not a product or business role" in result.reason


def test_a_title_that_contains_the_off_role_word_itself_is_fine():
    assert classify_posting("Solutions Engineer Intern", "Summer 2027", TITLES_DOC).decision == "match"
    assert classify_posting("Engineering Program Manager Intern", "Summer 2027", TITLES_DOC).decision == "match"


# --- single-word titles ------------------------------------------------------

def test_a_one_word_title_matches_any_posting_containing_the_whole_word():
    result = classify_posting("MBA Intern - Corporate Finance, Summer 2027", "", {**MBA_DOC, "titles": [
        {"title": "MBA", "group": "General", "aliases": [], "active": True},
    ]})
    assert result.decision == "match" and result.matched_title == "MBA"


def test_mba_single_word_rules():
    only_mba = {"exclude_words": ["marketing"], "titles": [
        {"title": "MBA", "group": "General", "aliases": [], "active": True},
    ]}
    assert classify_posting("MBA Intern - Corporate Finance, Summer 2027", "", only_mba).decision == "match"
    marketing = classify_posting("MBA Product Marketing Intern", "", only_mba)
    assert marketing.decision == "reject" and "marketing" in marketing.reason
    full_time = classify_posting("MBA Product Manager", "", only_mba)
    assert full_time.decision == "reject" and "full-time" in full_time.reason
    assert classify_posting("Mbaraki Site Intern", "", only_mba).decision == "reject"
    assert match_title("Mbaraki Site Intern", config.active_titles(only_mba)) is None


# --- an unclear location never overrides a failed title match -----------------

def test_reconsidering_a_rejection_needs_a_title_match():
    # The rejection was about location, but the title matches nothing.
    assert reconsider_rejection(
        "Location not specified", "AMS Lab Validation Intern", "", TITLES_DOC, LOCATIONS,
    ) is None
    kept = reconsider_rejection(
        "Location not specified", "Product Management Intern", "", TITLES_DOC, LOCATIONS,
    )
    assert kept is not None and kept.location_match == "unclear" and kept.matched_title
