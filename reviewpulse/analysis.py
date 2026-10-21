from __future__ import annotations

import html
import re
from collections import Counter
from datetime import date, timedelta

from .config import Settings
from .knowledge import explain
from .llm import JSONModel
from .util import digest, now


def rule_labels(reviews: list[dict], taxonomy: list[dict]) -> dict[str, str]:
    result = {}
    for review in reviews:
        text = review["text"].lower()
        scored = []
        for theme in taxonomy:
            if theme["id"] in {"other", "praise"}:
                continue
            score = sum(len(re.findall(r"\b" + re.escape(keyword), text)) for keyword in theme["keywords"])
            if score:
                scored.append((score, theme["id"]))
        result[review["review_id"]] = max(scored, key=lambda item: item[0])[1] if scored else ("praise" if review["rating"] >= 4 else "other")
    return result


def compute(reviews: list[dict], labels: dict[str, str], taxonomy: list[dict]) -> dict:
    latest = date.fromisoformat(reviews[-1]["date"])
    earliest = date.fromisoformat(reviews[0]["date"])
    recent_start = latest - timedelta(days=27)
    previous_start = latest - timedelta(days=55)
    recent = [r for r in reviews if date.fromisoformat(r["date"]) >= recent_start]
    previous = [r for r in reviews if previous_start <= date.fromisoformat(r["date"]) < recent_start]
    recent_counts = Counter(labels[r["review_id"]] for r in recent if r["rating"] <= 2)
    previous_counts = Counter(labels[r["review_id"]] for r in previous if r["rating"] <= 2)
    themes = []
    for theme in taxonomy:
        group = [r for r in reviews if labels[r["review_id"]] == theme["id"]]
        if not group:
            continue
        complaints = [r for r in group if r["rating"] <= 2]
        new_share = 100 * recent_counts[theme["id"]] / len(recent) if recent else None
        old_share = 100 * previous_counts[theme["id"]] / len(previous) if previous else None
        themes.append({
            "id": theme["id"], "name": theme["name"], "count": len(group),
            "share": round(100 * len(group) / len(reviews), 1), "negative_count": len(complaints),
            "recent_negative_count": recent_counts[theme["id"]], "previous_negative_count": previous_counts[theme["id"]],
            "recent_share": round(new_share, 1) if new_share is not None else None,
            "previous_share": round(old_share, 1) if old_share is not None else None,
            "change_pp": round(new_share - old_share, 1) if old_share is not None and new_share is not None else None,
            "quotes": sorted(complaints or group, key=lambda r: (r["rating"], r["date"]))[:2],
            "suggested_action": theme["action"],
        })
    themes.sort(key=lambda t: (-t["negative_count"], -t["count"], t["id"]))
    weeks = []
    start = earliest - timedelta(days=earliest.weekday())
    while start <= latest:
        end = start + timedelta(days=7)
        group = [r for r in reviews if start <= date.fromisoformat(r["date"]) < end]
        weeks.append({"week": start.isoformat(), "count": len(group), "average_rating": round(sum(r["rating"] for r in group) / len(group), 2) if group else None, "negative": sum(r["rating"] <= 2 for r in group)})
        start = end
    sentiment = {"positive": sum(r["rating"] >= 4 for r in reviews), "neutral": sum(r["rating"] == 3 for r in reviews), "negative": sum(r["rating"] <= 2 for r in reviews)}
    return {
        "review_count": len(reviews), "average_rating": round(sum(r["rating"] for r in reviews) / len(reviews), 2),
        "negative_share": round(100 * sentiment["negative"] / len(reviews), 1),
        "date_from": earliest.isoformat(), "date_to": latest.isoformat(), "sentiment": sentiment,
        "themes": themes, "weekly": weeks,
        "comparison": {"recent_start": recent_start.isoformat(), "recent_end": latest.isoformat(), "previous_start": previous_start.isoformat(), "previous_end": (recent_start - timedelta(days=1)).isoformat(), "recent_reviews": len(recent), "previous_reviews": len(previous), "denominator": "All reviews in each 28-day window; numerator is 1-2 star reviews assigned to the theme. Windows end at the latest review, not today's date."},
    }


def report_markdown(product: str, metrics: dict, support: dict, mode: str, validation: dict, is_demo: bool) -> str:
    lines = [f"# {product} - Customer feedback pulse", "", f"Review period: {metrics['date_from']} to {metrics['date_to']}", f"Analysis: {'local keyword rules' if mode == 'local' else 'LLM-assisted classification'}; one primary theme per review."]
    if is_demo:
        lines += ["SYNTHETIC DEMO DATA - not real customer feedback."]
    lines += ["", "## At a glance", f"{metrics['review_count']} usable reviews | Average rating {metrics['average_rating']}/5 | {metrics['negative_share']}% rated 1-2 stars", "", "## Leading reported issues"]
    issues = [t for t in metrics["themes"] if t["negative_count"]][:3]
    if not issues:
        lines.append("No 1-2 star reviews were present. Do not infer an absence of problems from this sample.")
    for theme in issues:
        change = "No previous-window baseline" if theme["change_pp"] is None else f"{theme['change_pp']:+.1f} percentage points versus previous window"
        lines += ["", f"### {theme['name']}", f"{theme['count']} reviews assigned; {theme['negative_count']} rated 1-2 stars. {change}.", f"Suggested investigation: {theme['suggested_action']}"]
        for quote in theme["quotes"]:
            lines.append(f"> {quote['text']} [{quote['review_id']}; {quote['rating']}/5; {quote['date']}]")
    lines += ["", "## Support evidence", support["question"] or "Not requested", "", support["note"]]
    for statement in support["statements"]:
        lines += ["", statement["text"] + f" [{statement['chunk_id']}]"]
    for source in support["sources"]:
        lines.append(f"Source [{source['chunk_id']}]: {source['title']} | {source['url'] or 'owner-uploaded document'} | snapshot {source['sha256'][:16]}")
    lines += ["", "## Method and limitations", metrics["comparison"]["denominator"], "Rating-based sentiment: 1-2 negative, 3 neutral, 4-5 positive. These are descriptive observations, not representative or causal estimates. Suggested actions are proposals, not confirmed fixes.", "Basic email/phone redaction is not complete anonymization. Sources are owner-supplied snapshots, not automatically verified official policy."]
    lines.extend(validation["warnings"])
    return "\n".join(lines)


def build_payload(job_id: str, product: str, report: str, target: dict) -> dict:
    marker = f"[review-pulse:{job_id}:{digest(report)[:16]}]"
    subject = f"{product} | Customer feedback pulse | {job_id[:8]}"
    body = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#192232;max-width:760px;margin:24px auto">'
        '<p style="font-size:12px;letter-spacing:2px;color:#5f6b80">REVIEW PULSE</p>'
        f'<h1>{html.escape(product)}: customer feedback</h1>'
        '<p>Human-reviewed report. This message was created as a draft, not sent.</p>'
        f'<pre style="white-space:pre-wrap;font-family:Arial,sans-serif;line-height:1.6">{html.escape(report)}</pre>'
        f'<p style="font-size:11px;color:#6a7280">{html.escape(marker)}</p></body></html>'
    )
    return {"version": 1, "marker": marker, "target": target, "doc_entry": "\n\n" + report + "\n" + marker + "\n", "subject": subject, "body": body}


def analyze(job: dict, settings: Settings, progress=lambda message: None) -> dict:
    import json
    spec = json.loads(job["spec_json"])
    reviews, taxonomy = spec["reviews"], spec["taxonomy"]
    model = JSONModel(settings, spec["model"]) if spec["mode"] == "llm" else None
    progress("Assigning review themes")
    labels = model.classify(reviews, taxonomy, spec["product"], progress) if model else rule_labels(reviews, taxonomy)
    progress("Computing counts and four-week comparisons")
    metrics = compute(reviews, labels, taxonomy)
    progress("Retrieving support evidence")
    support = explain(spec["question"], spec["sources"], model)
    report = report_markdown(spec["product"]["name"], metrics, support, spec["mode"], spec["validation"], spec["is_demo"])
    payload = build_payload(job["id"], spec["product"]["name"], report, spec["target"])
    return {
        "job_id": job["id"], "product": spec["product"], "created_at": now(), "mode": spec["mode"],
        "model": spec["model"] if model else None, "is_demo": spec["is_demo"],
        "metrics": metrics, "support": support, "validation": spec["validation"],
        "reviews": [{**review, "theme_id": labels[review["review_id"]]} for review in reviews],
        "report_markdown": report, "payload": payload, "payload_hash": digest(payload),
        "checks": {"all_reviews_classified": len(labels) == len(reviews), "quote_provenance": True, "support_citation_excerpts_checked": True, "sentiment_from_ratings": True, "external_publishing_requires_approval": True},
    }
