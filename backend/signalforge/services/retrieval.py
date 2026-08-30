"""Offline, version-scoped retrieval over review evidence.

The first MVP deliberately uses a transparent keyword ranker.  It is stable in
an offline demo, has no model download, and makes the returned evidence easy to
explain.  A semantic retriever can later implement the same ``retrieve``
contract without changing callers.
"""

from __future__ import annotations

import re
from collections import Counter

from signalforge.core.models import Evidence
from signalforge.db.connection import Database

_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Return lightweight search terms without requiring a Chinese tokenizer."""

    return [token.lower() for token in _TOKEN.findall(text) if token.strip()]


def _score(query_tokens: list[str], content: str, aspect: str | None) -> float:
    """Score exact token matches, including useful substring matches for Chinese."""

    if not query_tokens:
        return 0.0
    haystack = f"{content} {aspect or ''}".lower()
    token_counts = Counter(_tokens(haystack))
    score = 0.0
    for token in query_tokens:
        if token in token_counts:
            score += 1 + min(token_counts[token] - 1, 2) * 0.15
        elif token in haystack:
            score += 0.7 + min(haystack.count(token) - 1, 2) * 0.15
    return score


def keyword_ranked_reviews(
    db: Database, *, version_id: str, query: str, limit: int = 8
) -> list[Evidence]:
    """Rank reviews from exactly one snapshot using deterministic local matching."""

    if not query.strip() or limit < 1:
        return []
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    rows = db.execute(
        """
        SELECT id, content, rating, aspect, sentiment, redacted
        FROM reviews
        WHERE dataset_version_id = ?
        ORDER BY id ASC
        """,
        (version_id,),
    ).fetchall()
    ranked: list[tuple[float, Evidence]] = []
    for evidence_id, content, rating, aspect, sentiment, redacted in rows:
        score = _score(query_tokens, str(content), str(aspect) if aspect else None)
        if score <= 0:
            continue
        ranked.append(
            (
                score,
                Evidence(
                    id=str(evidence_id),
                    dataset_version_id=version_id,
                    content=str(content),
                    rating=int(rating) if rating is not None else None,
                    aspect=str(aspect) if aspect is not None else None,
                    sentiment=str(sentiment),
                    redacted=bool(redacted),
                ),
            )
        )
    if not ranked:
        return []

    maximum = ranked[0][0] if len(ranked) == 1 else max(score for score, _ in ranked)
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [
        evidence.model_copy(update={"relevance_score": round(score / maximum, 3)})
        for score, evidence in ranked[:limit]
    ]


def retrieve_evidence(
    db: Database, version_id: str, query: str, limit: int = 8
) -> list[Evidence]:
    """Retrieve review evidence using the offline keyword implementation."""

    return keyword_ranked_reviews(db, version_id=version_id, query=query, limit=limit)
