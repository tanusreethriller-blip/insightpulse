from dataclasses import replace
from datetime import date

import pytest

from reviewpulse.ingest import normalize, read_csv, redact
from reviewpulse.util import UserError


def test_csv_aliases_and_bom(settings):
    rows = read_csv(b'\xef\xbb\xbfcontent,at,score\n"This is a useful and reliable application",2026-10-01,5\n')
    clean, stats = normalize(rows, replace(settings, min_reviews=1))
    assert clean[0]["rating"] == 5
    assert stats["accepted_rows"] == 1


def test_required_csv_headers():
    with pytest.raises(UserError, match="rating"):
        read_csv(b"date,text\n2026-10-01,This review has more than five words\n")


def test_extra_csv_fields_rejected():
    with pytest.raises(UserError, match="extra fields"):
        read_csv(b"date,text,rating\n2026-10-01,unquoted,comma,5\n")


def test_duplicate_headers():
    with pytest.raises(UserError, match="duplicate column"):
        read_csv(b"text,date,rating, Text\nhello,2026-10-01,3,world")


def test_duplicate_reviews_removed(rows, settings):
    clean, stats = normalize(rows + [rows[0]], settings)
    assert len(clean) == len(rows)
    assert stats["drop_reasons"]["duplicate_text"] == 1


def test_invalid_and_future_dates_removed(rows, settings):
    invalid = [{**rows[0], "text": "This is another sample review with many words " + str(i), "date": d} for i, d in enumerate(["01/10/2026", "2045-01-01", "2026-13-40", "1999-01-01"])]
    _, stats = normalize(rows + invalid, settings, today=date(2026, 10, 21))
    assert stats["dropped_rows"] == 4


def test_fractional_missing_and_boolean_ratings_rejected(rows, settings):
    bad = [{**rows[0], "text": "This is another sample review about many topics " + str(i), "rating": value} for i, value in enumerate([2.5, None, True, 0, 8])]
    _, stats = normalize(rows + bad, settings)
    assert stats["drop_reasons"]["invalid_rating"] == 5


def test_pii_masking_and_external_ids_not_retained(settings):
    data = [{"date": "2026-10-01", "rating": 2, "review_id": "person@example.com", "text": "Please contact me at jane@example.com or +91 98765 43210 about this problem."}]
    clean, stats = normalize(data, replace(settings, min_reviews=1))
    assert "jane@example.com" not in str(clean)
    assert "98765" not in str(clean)
    assert "[EMAIL]" in clean[0]["text"]
    assert "[PHONE]" in clean[0]["text"]
    assert clean[0]["review_id"].startswith("r_")
    assert stats["redacted_rows"] == 1


def test_latest_twelve_weeks_retained(rows, settings):
    clean, stats = normalize(rows + [{"date": "2025-01-01", "rating": 1, "text": "This old review has sufficient words but predates the window."}], settings)
    assert len(clean) == len(rows)
    assert stats["drop_reasons"]["outside_lookback"] == 1


def test_row_limit(settings, rows):
    with pytest.raises(UserError, match="rows per job"):
        normalize(rows, replace(settings, max_rows=10))


def test_insufficient_data_is_not_fabricated(settings):
    with pytest.raises(UserError, match="Only 0 usable"):
        normalize([{"text": "Too short", "date": "2026-10-01", "rating": 3}], settings)


def test_timezone_aware_dates(settings):
    data = [{"date": "2026-09-30T23:45:00Z", "rating": 5, "text": "This is an example of a useful product review."}]
    clean, _ = normalize(data, replace(settings, min_reviews=1))
    assert clean[0]["date"] == "2026-10-01"
