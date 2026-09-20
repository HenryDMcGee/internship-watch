from src.companies import detect_feed, normalize_company_name, parse_pasted_companies


def test_normalize_strips_suffixes_and_parens():
    assert normalize_company_name("Arm Holdings") == "arm"
    assert normalize_company_name("Arm") == "arm"
    assert normalize_company_name("Alphabet (Google)") == "alphabet"
    assert normalize_company_name("Meta Platforms") == "meta"
    assert normalize_company_name("Dell Technologies") == "dell"
    assert normalize_company_name("HP Inc.") == "hp"
    assert normalize_company_name("Walmart Global Tech") == "walmart"


def test_normalize_is_case_and_space_insensitive():
    assert normalize_company_name("  NVIDIA  ") == normalize_company_name("nvidia")


def test_detect_feed_greenhouse():
    feed_type, feed_id = detect_feed("https://boards.greenhouse.io/acme")
    assert feed_type == "greenhouse"
    assert feed_id == "acme"


def test_detect_feed_lever():
    feed_type, feed_id = detect_feed("https://jobs.lever.co/acme")
    assert feed_type == "lever"
    assert feed_id == "acme"


def test_detect_feed_ashby():
    feed_type, feed_id = detect_feed("https://jobs.ashbyhq.com/acme")
    assert feed_type == "ashby"
    assert feed_id == "acme"


def test_detect_feed_smartrecruiters():
    feed_type, feed_id = detect_feed("https://careers.smartrecruiters.com/Acme")
    assert feed_type == "smartrecruiters"
    assert feed_id == "Acme"


def test_detect_feed_workday():
    feed_type, feed_id = detect_feed(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NvidiaExternalCareerSite/job/x"
    )
    assert feed_type == "workday"
    assert feed_id == "nvidia"


def test_detect_feed_amazon_jobs():
    feed_type, feed_id = detect_feed("https://www.amazon.jobs/en/jobs/1/foo")
    assert feed_type == "amazon_jobs"
    assert feed_id is None


def test_detect_feed_unknown_site():
    feed_type, feed_id = detect_feed("https://careers.example.com/jobs")
    assert feed_type is None
    assert feed_id is None


def test_detect_feed_none_url():
    assert detect_feed(None) == (None, None)
    assert detect_feed("") == (None, None)


def test_parse_pasted_companies_new_and_url():
    entries = parse_pasted_companies("Acme Corp, https://boards.greenhouse.io/acme", existing_names=[])
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "Acme Corp"
    assert entry.url == "https://boards.greenhouse.io/acme"
    assert entry.feed_type == "greenhouse"
    assert entry.match == "new"


def test_parse_pasted_companies_existing():
    entries = parse_pasted_companies("Apple", existing_names=["Apple"])
    assert entries[0].match == "existing"


def test_parse_pasted_companies_existing_matches_normalized_form():
    entries = parse_pasted_companies("Arm", existing_names=["Arm Holdings"])
    assert entries[0].match == "existing"


def test_parse_pasted_companies_duplicate_within_paste():
    entries = parse_pasted_companies("Acme\nAcme", existing_names=[])
    assert entries[0].match == "new"
    assert entries[1].match == "duplicate"


def test_parse_pasted_companies_unclear_for_bare_url():
    entries = parse_pasted_companies("https://example.com/careers", existing_names=[])
    assert entries[0].match == "unclear"
    assert entries[0].name is None


def test_parse_pasted_companies_skips_blank_lines():
    entries = parse_pasted_companies("Acme\n\n\nBeta", existing_names=[])
    assert [e.name for e in entries] == ["Acme", "Beta"]
