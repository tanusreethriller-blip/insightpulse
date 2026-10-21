from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .util import UserError, digest

EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{3,4}(?!\w)")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ALIASES = {
    "text": ("text", "review", "content", "comment", "feedback", "review_text"),
    "date": ("date", "created_at", "at", "review_date", "timestamp"),
    "rating": ("rating", "score", "stars", "star_rating"),
}


def redact(text: str) -> str:
    text = EMAIL.sub("[EMAIL]", text)
    def replace_phone(match: re.Match) -> str:
        value = match.group(0)
        return "[PHONE]" if 10 <= len(re.sub(r"\D", "", value)) <= 15 else value
    return PHONE.sub(replace_phone, text)


def read_csv(content: bytes) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserError("Save the CSV as UTF-8 and upload it again.") from exc
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        if not reader.fieldnames:
            raise UserError("The CSV has no header row.")
        normalized = [h.strip().lower() for h in reader.fieldnames]
        if len(set(normalized)) != len(normalized):
            raise UserError("The CSV contains duplicate column headers.")
        for target, names in ALIASES.items():
            if not any(name in normalized for name in names):
                raise UserError(f"Missing {target} column. Expected text, date, and rating.")
        rows = list(reader)
        if any(None in row for row in rows):
            raise UserError("A CSV row has extra fields. Quote text containing commas.")
        return rows
    except csv.Error as exc:
        raise UserError("Could not parse the CSV. Check quoting and row lengths.") from exc


def normalize(rows: list[dict], settings: Settings, today: date | None = None) -> tuple[list[dict], dict]:
    if not rows or len(rows) > settings.max_rows:
        raise UserError(f"Submit 1 to {settings.max_rows:,} rows per job.")
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    clean: list[dict] = []
    seen: set[str] = set()
    skipped: Counter = Counter()
    examples: list[dict] = []
    redacted_count = 0
    for number, source in enumerate(rows, 2):
        reason = ""
        try:
            if not isinstance(source, dict):
                raise ValueError("not_an_object")
            row = {str(key).strip().lower(): value for key, value in source.items()}
            values = {key: next((row[name] for name in names if name in row), None) for key, names in ALIASES.items()}
            raw_text = values["text"]
            if not isinstance(raw_text, str):
                raise ValueError("missing_text")
            text = re.sub(r"\s+", " ", CONTROL.sub("", raw_text)).strip()
            if not 5 <= len(text.split()) or len(text) > 6000:
                raise ValueError("text_length")
            # Deliberately reject ambiguous DD/MM dates and fractional ratings.
            raw_date = str(values["date"] or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", raw_date):
                raise ValueError("invalid_date")
            try:
                parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                d = parsed.astimezone(ZoneInfo(settings.timezone)).date() if parsed.tzinfo else parsed.date()
            except (ValueError, OverflowError):
                raise ValueError("invalid_date") from None
            if d > today or d.year < 2000:
                raise ValueError("date_out_of_range")
            rating = str(values["rating"] or "").strip()
            if not re.fullmatch(r"[1-5](?:\.0+)?", rating):
                raise ValueError("invalid_rating")
            rating_int = int(float(rating))
            safe_text = redact(text)
            redacted_count += safe_text != text
            key = safe_text.casefold()
            if key in seen:
                raise ValueError("duplicate_text")
            seen.add(key)
            clean.append({"review_id": "r_" + digest([safe_text, d.isoformat(), rating_int])[:20], "text": safe_text, "date": d.isoformat(), "rating": rating_int})
        except ValueError as exc:
            reason = str(exc)
        if reason:
            skipped[reason] += 1
            if len(examples) < 20:
                examples.append({"row": number, "reason": reason})
    if clean:
        latest = max(date.fromisoformat(row["date"]) for row in clean)
        cutoff = latest - timedelta(days=settings.lookback_weeks * 7 - 1)
        retained = [row for row in clean if date.fromisoformat(row["date"]) >= cutoff]
        skipped["outside_lookback"] += len(clean) - len(retained)
        clean = sorted(retained, key=lambda row: (row["date"], row["review_id"]))
    if len(clean) < settings.min_reviews:
        raise UserError(f"Only {len(clean)} usable reviews remain; need {settings.min_reviews}. Each needs 5+ words, an ISO date, and a 1-5 rating. Dropped: {dict(skipped)}")
    warnings = []
    if len(clean) < 100:
        warnings.append("Fewer than 100 usable reviews: treat the findings as exploratory.")
    span_days = (date.fromisoformat(clean[-1]["date"]) - date.fromisoformat(clean[0]["date"])).days + 1
    if span_days < 56:
        warnings.append("Less than eight weeks of date coverage; four-week comparisons may be incomplete.")
    return clean, {"input_rows": len(rows), "accepted_rows": len(clean), "dropped_rows": len(rows) - len(clean), "drop_reasons": {k: v for k, v in skipped.items() if v}, "redacted_rows": redacted_count, "examples": examples, "warnings": warnings}
