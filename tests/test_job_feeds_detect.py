from job.feeds.detect import detect_feed


def test_detect_feed_reuses_companies_logic():
    from companies import detect_feed as companies_detect_feed

    assert detect_feed is companies_detect_feed


def test_detect_feed_greenhouse():
    assert detect_feed("https://boards.greenhouse.io/acme") == ("greenhouse", "acme")


def test_detect_feed_unknown():
    assert detect_feed("https://careers.example.com") == (None, None)
