"""Summarize explicitly recorded evidence reviews; never infer human scores from model text."""

import argparse
import json
import statistics
from pathlib import Path

FIELDS = ("answer_correct", "citation_supported", "no_answer_handled", "conflict_handled")


def summarize(results, review):
    if review.get("dataset") != results["summary"]["dataset"] or review.get("reviewer_kind") not in {"developer_preliminary", "agent", "human"} or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("Explicit matching dataset and reviewer identity/kind required")
    cases = {case["case_id"]: case for case in results["cases"]}
    seen, metrics = set(), {field: [] for field in FIELDS}
    for row in review.get("cases", []):
        case_id = row.get("case_id")
        if case_id not in cases or case_id in seen or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("Known unique case and evidence-review reason required")
        if not cases[case_id].get("answer"):
            raise ValueError("Cannot grade a failed or missing answer")
        for field in FIELDS:
            value = row.get(field)
            if value is not None and type(value) is not bool:
                raise ValueError("Review fields must be boolean or null")
            if field == "no_answer_handled" and value is not None and cases[case_id]["kind"] != "no_answer":
                raise ValueError("Refusal grading belongs to no-answer cases")
            if field == "conflict_handled" and value is not None and cases[case_id]["kind"] != "conflict":
                raise ValueError("Conflict grading belongs to conflict cases")
            if value is not None:
                metrics[field].append(value)
        seen.add(case_id)
    return {
        "dataset": review["dataset"],
        "reviewer_kind": review["reviewer_kind"],
        "reviewer": review["reviewer"],
        "human_verified": review["reviewer_kind"] == "human",
        "scored_answers": len(metrics["answer_correct"]),
        "answer_correctness": statistics.mean(metrics["answer_correct"]) if metrics["answer_correct"] else None,
        "citation_support": statistics.mean(metrics["citation_supported"]) if metrics["citation_supported"] else None,
        "no_answer_handling": statistics.mean(metrics["no_answer_handled"]) if metrics["no_answer_handled"] else None,
        "conflict_handling": statistics.mean(metrics["conflict_handled"]) if metrics["conflict_handled"] else None,
        "metric_denominators": {field: len(values) for field, values in metrics.items()},
        "unreviewed_cases": [key for key in cases if key not in seen],
        "failed_or_missing_answers": [key for key, case in cases.items() if not case.get("answer")],
        "limitations": "Fixed small synthetic corpus; agent and developer reviews are not human reviews or general accuracy. Missing cases are not passes.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(json.loads(args.results.read_text(encoding="utf-8")), json.loads(args.review.read_text(encoding="utf-8")))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
