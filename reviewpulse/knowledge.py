from __future__ import annotations

import math
import re
from collections import Counter

from .llm import JSONModel
from .util import UserError

STOP = set("a an the is are was were to of in on for and or with my how what why do does can i it your you this that me be from have has about please".split())


def tokens(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOP and len(word) > 1]


def chunks(sources: list[dict]) -> list[dict]:
    result = []
    for source in sources:
        text = source["content"]
        # Fixed character windows preserve exact substrings for citation checks.
        for start in range(0, len(text), 1050):
            excerpt = text[start:start + 1400].strip()
            if excerpt:
                result.append({"chunk_id": f"{source['id']}:{start}", "source_id": source["id"], "title": source["title"], "url": source["url"], "sha256": source["sha256"], "text": excerpt})
    return result


def retrieve(question: str, sources: list[dict], limit: int = 4) -> list[dict]:
    documents = chunks(sources)
    query = set(tokens(question))
    if not documents or not query:
        return []
    counts = [Counter(tokens(doc["title"] + " " + doc["text"])) for doc in documents]
    average = sum(sum(c.values()) for c in counts) / len(counts) or 1
    frequencies = {term: sum(term in c for c in counts) for term in query}
    scores = []
    for doc, count in zip(documents, counts):
        length = sum(count.values())
        score = 0.0
        for term in query:
            frequency = count[term]
            if not frequency:
                continue
            inverse = math.log(1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += inverse * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            scores.append((score, doc))
    return [doc for _, doc in sorted(scores, key=lambda item: item[0], reverse=True)[:limit]]


def explain(question: str, sources: list[dict], model: JSONModel | None = None) -> dict:
    if not question.strip():
        return {"status": "not_requested", "question": "", "statements": [], "sources": [], "note": "No support question was supplied."}
    evidence = retrieve(question, sources)
    if not evidence:
        return {"status": "insufficient_evidence", "question": question, "statements": [], "sources": [], "note": "No matching documentation was found. Add relevant product documentation; do not use reviews as policy evidence."}
    by_id = {item["chunk_id"]: item for item in evidence}
    if model is None:
        statements = [{"text": item["text"], "chunk_id": item["chunk_id"], "quote": item["text"]} for item in evidence[:3]]
        note = "Local mode: verbatim retrieved excerpts, not an AI-written answer. Confirm that each excerpt answers the question."
    else:
        result = model.ask(
            "Answer the support question using ONLY the supplied documentation excerpts. "
            "Each statement must be backed by one provided chunk_id and a short EXACT verbatim quote from that chunk. "
            "Do not add pricing, promises, procedures, dates, or eligibility absent from the evidence. "
            "When the evidence cannot answer, return an empty statements array. "
            "Output {\"statements\":[{\"text\":\"One supported sentence\",\"chunk_id\":\"...\",\"quote\":\"Exact supporting excerpt\"}]}, with at most 5 statements.",
            {"question": question, "evidence": evidence},
        )
        statements = result.get("statements")
        if not isinstance(statements, list) or len(statements) > 5:
            raise UserError("Support explanation has an invalid structure. Analysis stopped.")
        for item in statements:
            if not isinstance(item, dict) or item.get("chunk_id") not in by_id:
                raise UserError("Support explanation cited an unknown source. Analysis stopped.")
            text, quote = item.get("text"), item.get("quote")
            if not isinstance(text, str) or not 5 <= len(text) <= 1000 or not isinstance(quote, str) or not 8 <= len(quote) <= 1200 or quote not in by_id[item["chunk_id"]]["text"]:
                raise UserError("A support citation was not an exact source excerpt. Analysis stopped.")
        note = "Citation identifiers and exact excerpts were checked. This does not prove that a paraphrase follows from its source; human verification is required."
    used = {s["chunk_id"] for s in statements}
    return {"status": "evidence_found" if statements else "insufficient_evidence", "question": question, "statements": statements, "sources": [item for item in evidence if item["chunk_id"] in used], "note": note}
