"""Feed-content hashing (job/feed_snapshot.py) -- the part of issue #51 that
decides whether a company's feed is "the same as last time" without touching
the database.
"""
from job import feed_snapshot
from job.models import FetchedPosting


def _posting(**overrides) -> FetchedPosting:
    row = dict(
        company_name="Acme", title="MBA Product Manager Intern", url="https://boards.greenhouse.io/acme/jobs/1",
        location_text="San Francisco, CA", posted_date="2026-09-01", external_id="1",
    )
    row.update(overrides)
    return FetchedPosting(**row)


def test_identical_postings_hash_the_same():
    a = [_posting()]
    b = [_posting()]
    assert feed_snapshot.hash_postings(a) == feed_snapshot.hash_postings(b)


def test_hash_ignores_the_raw_field():
    # `raw` carries the vendor's untouched record, including whatever
    # response-only timestamps or request ids it threw in -- those must
    # never make an otherwise-identical feed look changed.
    a = [_posting(raw={"updated_at": "2026-09-01T00:00:00Z", "request_id": "abc"})]
    b = [_posting(raw={"updated_at": "2026-09-02T11:00:00Z", "request_id": "xyz"})]
    assert feed_snapshot.hash_postings(a) == feed_snapshot.hash_postings(b)


def test_hash_ignores_listing_order():
    first = _posting(external_id="1", url="https://boards.greenhouse.io/acme/jobs/1")
    second = _posting(external_id="2", title="Data Analyst Intern", url="https://boards.greenhouse.io/acme/jobs/2")
    assert feed_snapshot.hash_postings([first, second]) == feed_snapshot.hash_postings([second, first])


def test_hash_changes_when_a_job_is_added():
    a = [_posting()]
    b = [_posting(), _posting(external_id="2", title="Data Analyst Intern", url="https://boards.greenhouse.io/acme/jobs/2")]
    assert feed_snapshot.hash_postings(a) != feed_snapshot.hash_postings(b)


def test_hash_changes_when_a_meaningful_field_changes():
    a = [_posting(title="MBA Product Manager Intern")]
    b = [_posting(title="Senior MBA Product Manager Intern")]
    assert feed_snapshot.hash_postings(a) != feed_snapshot.hash_postings(b)


def test_all_found_sentinel_contains_everything():
    assert "https://anything.example/1" in feed_snapshot.ALL_FOUND
    assert "" in feed_snapshot.ALL_FOUND
