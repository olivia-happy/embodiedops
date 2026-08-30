"""Application errors that are safe to expose through the API layer."""


class EvidenceInsufficientError(ValueError):
    """Raised when a conclusion would not have enough supporting evidence."""

    def __init__(
        self,
        message: str = "证据不足，建议补充样本或调整筛选条件。",
        *,
        evidence_count: int | None = None,
        required_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence_count = evidence_count
        self.required_count = required_count
