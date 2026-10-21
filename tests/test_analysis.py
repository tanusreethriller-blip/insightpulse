import json
from dataclasses import replace

import pytest

from reviewpulse.analysis import compute, rule_labels
from reviewpulse.ingest import normalize
from reviewpulse.knowledge import explain, retrieve
from reviewpulse.llm import JSONModel
from reviewpulse.util import UserError, digest
from reviewpulse.worker import Worker

SOURCE = {"id": "S123", "title": "Subscription cancellation policy", "url": "https://example.com/billing", "content": "Subscriptions renew monthly. Cancel your subscription from the Billing page. Cancellation stops the next renewal but does not immediately end paid access.", "sha256": "abcd"}


def test_metrics_count_all_reviews(settings, rows):
    clean, _ = normalize(rows, settings)
    labels = rule_labels(clean, settings.taxonomy())
    m = compute(clean, labels, settings.taxonomy())
    assert sum(t["count"] for t in m["themes"]) == len(clean)
    assert sum(m["sentiment"].values()) == len(clean)
    assert sum(t["negative_count"] for t in m["themes"]) == m["sentiment"]["negative"]
    assert sum(w["count"] for w in m["weekly"]) == len(clean)
    assert all(q in clean for t in m["themes"] for q in t["quotes"])


def test_empty_weeks_have_no_rating(settings):
    reviews = [
        {"review_id":"one", "text":"A crash happens each time I save", "date":"2026-08-01", "rating":1},
        {"review_id":"two", "text":"This is a useful and excellent application", "date":"2026-10-01", "rating":5},
    ]
    m = compute(reviews, {"one":"reliability", "two":"praise"}, settings.taxonomy())
    assert any(w["count"] == 0 and w["average_rating"] is None for w in m["weekly"])


def test_missing_baseline_is_none(settings):
    data = [{"review_id":"one", "text":"A crash happens each time I save", "date":"2026-10-01", "rating":1}]
    m = compute(data, {"one":"reliability"}, settings.taxonomy())
    assert m["themes"][0]["change_pp"] is None
    assert m["comparison"]["previous_reviews"] == 0


def test_grounded_local_answer_uses_exact_excerpts():
    answer = explain("How do I cancel my subscription?", [SOURCE])
    assert answer["status"] == "evidence_found"
    assert all(s["quote"] in SOURCE["content"] for s in answer["statements"])
    assert "verbatim" in answer["note"]


def test_no_sources_abstains():
    result = explain("What is the refund policy?", [])
    assert result["status"] == "insufficient_evidence"
    assert not result["statements"]


def test_unrelated_sources_abstain():
    assert retrieve("giraffe elephant", [SOURCE]) == []


def test_fabricated_citation_rejected():
    class BadModel:
        def ask(self, *args):
            return {"statements":[{"text":"Refunds are always guaranteed.", "chunk_id":"S123:0", "quote":"Guaranteed refunds for everyone"}]}
    with pytest.raises(UserError, match="exact source"):
        explain("subscription cancel", [SOURCE], BadModel())


def test_unknown_citation_rejected():
    class BadModel:
        def ask(self, *args):
            return {"statements":[{"text":"Subscriptions renew monthly.", "chunk_id":"FAKE", "quote":"Subscriptions renew monthly."}]}
    with pytest.raises(UserError, match="unknown source"):
        explain("subscription cancel", [SOURCE], BadModel())


def test_llm_labels_require_complete_coverage(settings, rows, monkeypatch):
    model = JSONModel(replace(settings, llm_api_key="fake"))
    monkeypatch.setattr(model, "ask", lambda *a: {"labels": []})
    with pytest.raises(UserError, match="every review"):
        model.classify(normalize(rows, settings)[0], settings.taxonomy(), {}, lambda message: None)


def test_llm_labels_reject_unknown_theme(settings, rows, monkeypatch):
    clean, _ = normalize(rows, settings)
    model = JSONModel(settings)
    monkeypatch.setattr(model, "ask", lambda *a: {"labels": [{"review_id": r["review_id"], "theme_id": "invented"} for r in clean]})
    with pytest.raises(UserError, match="unknown theme"):
        model.classify(clean, settings.taxonomy(), {}, lambda message: None)


def test_report_payload_is_hash_bound_and_html_escaped(settings, service, store, rows):
    for row in rows:
        row["text"] += ' <script>alert("bad")</script>'
    job = service.submit(rows, "Uploaded reviews")
    Worker(store, settings).once()
    result = json.loads(store.job(job["id"])["result_json"])
    assert result["payload_hash"] == digest(result["payload"])
    assert '<script>' not in result["payload"]["body"]
    assert "&lt;script&gt;" in result["payload"]["body"]


def test_knowledge_is_snapshotted_at_submission(service, settings, store, rows):
    source = store.add_source(SOURCE["title"], SOURCE["url"], SOURCE["content"])
    job = service.submit(rows, "Snapshot test", "How to cancel subscriptions?", [source["id"]])
    store.delete_source(source["id"])
    Worker(store, settings).once()
    result = json.loads(store.job(job["id"])["result_json"])
    assert result["support"]["status"] == "evidence_found"


def test_demo_sources_never_used_for_real_jobs(service, store, rows):
    source = store.add_source(SOURCE["title"], "", SOURCE["content"], True)
    with pytest.raises(UserError, match="Synthetic demo"):
        service.submit(rows, "Real input", source_ids=[source["id"]])
    job = service.submit(rows, "Real input")
    assert json.loads(job["spec_json"])["sources"] == []


def test_local_analysis_does_not_make_http_calls(service, store, rows, settings, monkeypatch):
    import httpx
    def fail(*args, **kwargs):
        raise AssertionError("Local analysis must not use HTTP")
    monkeypatch.setattr(httpx.Client, "post", fail)
    job = service.submit(rows, "No network")
    Worker(store, settings).once()
    assert store.job(job["id"])["status"] == "ready"
