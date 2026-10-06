"""Negative evidence must not be manufactured from an omitted shared property."""

from knowledge_base_extension.feedback import feedback_result


def check(quote, relation, *, ids=None):
    return {"learner_quote": quote, "relation": relation, "explanation": "Synthetic comparison", "citation_ids": ids or [], "evidence_quote": ""}


def test_audit_uncertainty_does_not_repeat_unsupported_property_contradiction():
    shared_property = "Sorted Set 用于不重复成员"
    draft = {"complete": False, "checks": [check(shared_property, "contradicted", ids=["synthetic-source"])]}
    audit = {"complete": False, "checks": [check(shared_property, "uncertain")]}
    result = feedback_result(draft, audit)
    assert result["evaluation"] == "insufficient_evidence"
    assert result["feedback_checks"][0]["relation"] == "uncertain"
    assert "与资料矛盾" not in result["feedback"]


def test_uncertainty_keeps_independent_actual_errors_and_positive_cannot_erase_them():
    shared, wrong = "Sorted Set 用于不重复成员", "Set 按分数排序"
    draft = {"complete": False, "checks": [check(shared, "contradicted", ids=["synthetic-source"]), check(wrong, "contradicted", ids=["synthetic-source"])]}
    audit = {"complete": False, "checks": [check(shared, "uncertain"), check(wrong, "supported", ids=["synthetic-source"])]}
    result = feedback_result(draft, audit)
    assert result["evaluation"] == "needs_work"
    by_quote = {c["learner_quote"]: c["relation"] for c in result["feedback_checks"]}
    assert by_quote == {shared: "uncertain", wrong: "contradicted"}
