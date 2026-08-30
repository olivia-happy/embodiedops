"""Conservative cleaning for user-review snapshots."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
UNSAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")
WHITESPACE = re.compile(r"\s+")
VALID_SENTIMENTS = frozenset({"positive", "neutral", "negative", "unknown"})


@dataclass(frozen=True, slots=True)
class NormalizedReview:
    """A review that is safe to persist in the local demonstration database."""

    id: str
    content: str
    rating: int
    aspect: str
    sentiment: str
    redacted: bool


class NormalizationError(ValueError):
    """Raised when a source row cannot safely become a normalized review."""


def _required(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if value is None or not str(value).strip():
        raise NormalizationError(f"{field} is required")
    return str(value).strip()


def _safe_id(raw_id: str, source_slug: str) -> str:
    """Create a deterministic database ID without allowing path or control characters."""

    slug = UNSAFE_ID.sub("-", source_slug.strip().lower()).strip("-") or "source"
    cleaned = UNSAFE_ID.sub("-", raw_id.strip()).strip("-").lower()
    if not cleaned:
        cleaned = sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    return f"{slug}-{cleaned[:96]}"


def _clean_optional(value: object | None, default: str) -> str:
    if value is None:
        return default
    cleaned = WHITESPACE.sub(" ", str(value).strip())
    return cleaned or default


def normalize_review(raw: Mapping[str, object], *, source_slug: str = "asap") -> NormalizedReview:
    """Normalize one mapped review row and redact common direct contact details.

    The importer fails closed for missing content or invalid ratings. It only
    replaces values that match high-confidence phone/email patterns and records
    that fact in ``redacted`` for an auditable display decision later.
    """

    review_id = _required(raw, "review_id")
    original = WHITESPACE.sub(" ", _required(raw, "review"))
    content = PHONE.sub("[PHONE]", original)
    content = EMAIL.sub("[EMAIL]", content)
    try:
        rating = int(_required(raw, "rating"))
    except ValueError as error:
        raise NormalizationError("rating must be an integer from 1 to 5") from error
    if not 1 <= rating <= 5:
        raise NormalizationError("rating must be an integer from 1 to 5")

    sentiment = _clean_optional(raw.get("sentiment"), "unknown").lower()
    if sentiment not in VALID_SENTIMENTS:
        sentiment = "unknown"

    return NormalizedReview(
        id=_safe_id(review_id, source_slug),
        content=content,
        rating=rating,
        aspect=_clean_optional(raw.get("aspect"), "unknown"),
        sentiment=sentiment,
        redacted=content != original,
    )
