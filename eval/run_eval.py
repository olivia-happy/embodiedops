"""Run transparent offline review and decision-memo regression evaluations."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rounded_ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def classify(text: str) -> tuple[str, str]:
    lowered = text.lower()
    aspect_words = {
        "service": ("客服", "排队", "回复"),
        "app": ("应用", "页面", "功能", "界面"),
        "price": ("价格", "套餐", "收费", "优惠"),
        "delivery": ("配送", "物流", "包装"),
    }
    aspect = next(
        (name for name, words in aspect_words.items() if any(word in lowered for word in words)),
        "unknown",
    )
    if "脱敏" in lowered:
        sentiment = "unknown"
    elif any(word in lowered for word in ("慢", "久", "闪退", "不", "高", "晚", "一般")):
        sentiment = "negative"
    elif any(word in lowered for word in ("清楚", "快", "透明", "及时", "完好")):
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return aspect, sentiment


def macro_f1(gold: Iterable[str], predicted: Iterable[str]) -> float:
    gold_list, predicted_list = list(gold), list(predicted)
    labels = sorted(set(gold_list) | set(predicted_list))
    scores: list[float] = []
    for label in labels:
        tp = sum(
            g == label and p == label
            for g, p in zip(gold_list, predicted_list, strict=True)
        )
        fp = sum(
            g != label and p == label
            for g, p in zip(gold_list, predicted_list, strict=True)
        )
        fn = sum(
            g == label and p != label
            for g, p in zip(gold_list, predicted_list, strict=True)
        )
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0.0)
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def evaluate_reviews(path: Path) -> dict[str, object]:
    """Preserve the existing reviewed keyword-classification regression metrics."""

    rows = _load_jsonl(path)
    predicted = [classify(str(row["text"])) for row in rows]
    predicted_negative = [
        row
        for row, (_, sentiment) in zip(rows, predicted, strict=True)
        if sentiment == "negative"
    ]
    return {
        "dataset": path.name,
        "row_count": len(rows),
        "aspect_macro_f1": macro_f1(
            (str(row["gold_aspect"]) for row in rows),
            (value[0] for value in predicted),
        ),
        "sentiment_macro_f1": macro_f1(
            (str(row["gold_sentiment"]) for row in rows),
            (value[1] for value in predicted),
        ),
        "evidence_precision": _rounded_ratio(
            sum(bool(row["supports_claim"]) for row in predicted_negative),
            len(predicted_negative),
        ),
        "unsupported_claim_rate": _rounded_ratio(
            sum(not bool(row["supports_claim"]) for row in predicted_negative),
            len(predicted_negative),
        ),
        "feedback_distribution": {"confirmed": 0, "edited": 0, "rejected": 0},
        "class_distribution": dict(Counter(str(row["gold_aspect"]) for row in rows)),
    }


def _numeric_values_match(authoritative: object, predicted: object) -> bool:
    if (
        isinstance(authoritative, (int, float))
        and not isinstance(authoritative, bool)
        and isinstance(predicted, (int, float))
        and not isinstance(predicted, bool)
    ):
        return math.isclose(float(authoritative), float(predicted), rel_tol=0.0, abs_tol=1e-9)
    return authoritative == predicted


def _plan_specificity_checks(
    gold_plan: dict[str, object], prediction_plan: object
) -> tuple[int, int]:
    """Score four explicit requirements authored in the synthetic fixture."""

    if not isinstance(prediction_plan, dict):
        return 0, 4

    expected_subproblems = set(gold_plan["candidate_subproblems"])
    predicted_subproblems = set(prediction_plan.get("candidate_subproblems", []))
    expected_fields = set(gold_plan["required_collection_fields"])
    predicted_fields = set(prediction_plan.get("collection_fields", []))
    expected_minimum = max(3, int(gold_plan["minimum_evidence_per_subproblem"]))
    predicted_minimum = prediction_plan.get("minimum_evidence_per_subproblem")
    condition = prediction_plan.get("reassessment_condition")
    keywords = [str(item) for item in gold_plan["reassessment_keywords"]]

    checks = (
        bool(expected_subproblems) and expected_subproblems.issubset(predicted_subproblems),
        bool(expected_fields) and expected_fields.issubset(predicted_fields),
        isinstance(predicted_minimum, int) and predicted_minimum >= expected_minimum,
        isinstance(condition, str)
        and bool(condition.strip())
        and all(keyword in condition for keyword in keywords),
    )
    return sum(checks), len(checks)


def evaluate_decision_memos(path: Path) -> dict[str, object]:
    """Measure fixed candidate outputs against separately authored expectations.

    This function does not call a model or copy gold values into predictions.
    Each synthetic JSONL row contains separate ``gold`` and ``prediction``
    objects so the metric implementation can be regression-tested without
    network access. This function does not execute the production validator.
    """

    rows = _load_jsonl(path)
    correct_subproblem_evidence = 0
    predicted_subproblem_evidence = 0
    covered_citations = 0
    required_citations = 0
    unsupported_numeric_claims = 0
    numeric_claims = 0
    correct_statuses = 0
    decision_status_cases = 0
    correct_generation_outcomes = 0
    specific_plan_checks = 0
    total_plan_checks = 0

    for row in rows:
        gold = row["gold"]
        prediction = row["prediction"]
        authoritative = row["authoritative"]
        assert isinstance(gold, dict)
        assert isinstance(prediction, dict)
        assert isinstance(authoritative, dict)

        gold_outcome = gold.get("job_outcome", "completed")
        prediction_outcome = prediction.get("job_outcome", "completed")
        if gold_outcome not in {"completed", "failed"}:
            raise ValueError("gold job_outcome must be completed or failed")
        if prediction_outcome not in {"completed", "failed"}:
            raise ValueError("prediction job_outcome must be completed or failed")
        gold_status = gold["decision_status"]
        prediction_status = prediction["decision_status"]
        if gold_outcome == "failed" and gold_status is not None:
            raise ValueError("a failed gold generation cannot have a memo status")
        if prediction_outcome == "failed" and prediction_status is not None:
            raise ValueError("a failed prediction cannot have a memo status")
        correct_generation_outcomes += prediction_outcome == gold_outcome

        gold_support = {
            str(subproblem["name"]): set(subproblem["supporting_evidence_ids"])
            for subproblem in gold["subproblems"]
        }
        for subproblem in prediction["subproblems"]:
            name = str(subproblem["name"])
            expected_ids = gold_support.get(name, set())
            for evidence_id in subproblem["supporting_evidence_ids"]:
                predicted_subproblem_evidence += 1
                correct_subproblem_evidence += evidence_id in expected_ids

        expected_citations = set(gold["required_citation_ids"])
        predicted_citations = set(prediction["citation_ids"])
        required_citations += len(expected_citations)
        covered_citations += len(expected_citations & predicted_citations)

        facts = authoritative["numeric_facts"]
        assert isinstance(facts, dict)
        for claim in prediction["numeric_claims"]:
            numeric_claims += 1
            field = str(claim["field"])
            if field not in facts or not _numeric_values_match(facts[field], claim["value"]):
                unsupported_numeric_claims += 1

        if gold_outcome == "completed":
            decision_status_cases += 1
            correct_statuses += prediction_status == gold_status
        if gold_status == "needs_evidence":
            gold_plan = gold["evidence_plan"]
            assert isinstance(gold_plan, dict)
            passed, available = _plan_specificity_checks(
                gold_plan, prediction["evidence_plan"]
            )
            specific_plan_checks += passed
            total_plan_checks += available

    return {
        "decision_memo_dataset": path.name,
        "decision_memo_case_count": len(rows),
        "decision_memo_case_distribution": dict(
            Counter(tag for row in rows for tag in row["case_tags"])
        ),
        "subproblem_evidence_precision": _rounded_ratio(
            correct_subproblem_evidence, predicted_subproblem_evidence
        ),
        "citation_completeness": _rounded_ratio(covered_citations, required_citations),
        "unsupported_numeric_rate": _rounded_ratio(
            unsupported_numeric_claims, numeric_claims
        ),
        "decision_status_case_count": decision_status_cases,
        "decision_status_accuracy": _rounded_ratio(
            correct_statuses, decision_status_cases
        ),
        "generation_outcome_case_count": len(rows),
        "generation_outcome_accuracy": _rounded_ratio(
            correct_generation_outcomes, len(rows)
        ),
        "evidence_plan_specificity": _rounded_ratio(
            specific_plan_checks, total_plan_checks
        ),
    }


def main() -> None:
    result = {
        **evaluate_reviews(ROOT / "gold_reviews.jsonl"),
        **evaluate_decision_memos(ROOT / "gold_decision_memos.jsonl"),
        "limitations": (
            "小型合成离线回归集，尚未经过独立复核；本脚本不运行生产验证器，"
            "固定候选输出不代表当前模型或生产质量。"
        ),
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (ROOT / "results.json").write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
