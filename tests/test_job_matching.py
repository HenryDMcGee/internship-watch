from job.matching import classify_posting, cycle_status, find_exclude_word, match_title

TITLES_DOC = {
    "version": 1,
    "exclude_words": ["marketing"],
    "titles": [
        {"title": "MBA Product Manager Intern", "group": "Core PM", "aliases": [], "active": True},
        {"title": "Associate Product Manager Intern", "group": "Core PM", "aliases": ["APM Intern"], "active": True},
        {"title": "Old Retired Title", "group": "Core PM", "aliases": [], "active": False},
    ],
}


def test_find_exclude_word_case_insensitive():
    assert find_exclude_word("Marketing Product Manager Intern", ["marketing"]) == "marketing"
    assert find_exclude_word("Product Manager Intern", ["marketing"]) is None


def test_match_title_substring_case_insensitive():
    match = match_title("2027 Summer mba product manager intern - SF", TITLES_DOC["titles"])
    assert match is not None
    assert match.matched_title == "MBA Product Manager Intern"


def test_match_title_via_alias():
    match = match_title("Summer 2027 APM Intern", TITLES_DOC["titles"])
    assert match.matched_title == "Associate Product Manager Intern"


def test_match_title_ignores_inactive_titles():
    assert match_title("Old Retired Title", TITLES_DOC["titles"]) is None


def test_match_title_no_match():
    assert match_title("Software Engineer Intern", TITLES_DOC["titles"]) is None


def test_cycle_status_match():
    assert cycle_status("MBA Product Manager Intern, Summer 2027") == "match"


def test_cycle_status_rejects_wrong_class_year():
    assert cycle_status("MBA Class of 2027 Product Manager") == "reject"


def test_cycle_status_rejects_other_summer():
    assert cycle_status("Product Manager Intern, Summer 2026") == "reject"


def test_cycle_status_unclear_with_no_year():
    assert cycle_status("MBA Product Manager Intern") == "unclear"


def test_classify_posting_match():
    result = classify_posting("MBA Product Manager Intern, Summer 2027", "", TITLES_DOC)
    assert result.decision == "match"
    assert result.matched_title == "MBA Product Manager Intern"


def test_classify_posting_rejects_excluded_word():
    result = classify_posting("Marketing Product Manager Intern, Summer 2027", "", TITLES_DOC)
    assert result.decision == "reject"
    assert "excluded" in result.reason


def test_classify_posting_rejects_no_title_match():
    result = classify_posting("Random Job Title", "", TITLES_DOC)
    assert result.decision == "reject"


def test_classify_posting_rejects_wrong_cycle_in_description():
    result = classify_posting("MBA Product Manager Intern", "For the MBA Class of 2027.", TITLES_DOC)
    assert result.decision == "reject"
    assert result.matched_title == "MBA Product Manager Intern"


def test_classify_posting_unclear_cycle_is_stored_for_phase_3():
    result = classify_posting("MBA Product Manager Intern", "", TITLES_DOC)
    assert result.decision == "unclear"
    assert result.matched_title == "MBA Product Manager Intern"
