"""Ground model review in actual learner text; derive the verdict in code."""

from .store import KnowledgeError

GRADING_VERSION = 3

GRADING_POLICY = (
    "For feedback, learner_answer is the ONLY student's answer. reference_answer is private expected content, NOT student text. "
    "Check the actual subject-property relationships, not keyword presence. Swapped subjects/definitions are contradicted even when all correct keywords appear. "
    "Properties can be shared: evidence stating a property for one entity does NOT establish its absence in another. "
    "Do not infer exclusivity, negation or impossibility from an omitted fact or a recommended example. "
    "Mark contradicted only when supplied evidence actually opposes the learner's claim; otherwise uncertain, not a confident negative. "
    "Quote one atomic learner claim per check. Audit each draft quote separately, keeping the same exact learner_quote when correcting an unsupported negative. "
    "Every check must quote an EXACT contiguous substring of learner_answer (case/punctuation unchanged), "
    "and an EXACT substring of the cited evidence text, not the reference answer. Never invent what the student said. "
    "Explain specific contradictions or omissions, not a rewrite of the correct answer presented as student's work. "
    "Use uncertain with empty evidence_quote/citation_ids when evidence cannot establish a verdict. "
    "complete is true ONLY when every essential requested point is actually answered correctly; otherwise false. "
    "For learning-feedback-audit, independently reread the raw learner_answer and evidence; actively challenge draft_checks. "
    "Correct any mistaken positive judgement; do not rubber-stamp the draft. No overall correctness prose is requested."
)


def grounded_checks(review, answer, evidence):
    by_id = {e["citation_id"]: e["text"] for e in evidence}
    for check in review["checks"]:
        if not check["learner_quote"].strip() or check["learner_quote"] not in answer:
            raise KnowledgeError("learning_feedback_not_grounded")
        ids, quote = check["citation_ids"], check["evidence_quote"]
        if ids:
            if len(ids) != 1 or ids[0] not in by_id or not quote.strip() or quote not in by_id[ids[0]]:
                raise KnowledgeError("learning_feedback_not_grounded")
        elif check["relation"] != "uncertain" or quote:
            raise KnowledgeError("learning_feedback_not_grounded")
    return review


def feedback_result(draft, audit):
    original = draft["checks"] + audit["checks"]
    # Explicit lack of evidence is not a positive verdict. It withdraws an
    # unsupported negative for that exact claim, never an independent error.
    uncertain = {c["learner_quote"]: c for c in original if c["relation"] == "uncertain"}
    all_checks = [uncertain[c["learner_quote"]] if c["learner_quote"] in uncertain and c["relation"] in {"contradicted", "missing"} else c for c in original]
    relations = {c["relation"] for c in all_checks}
    if relations & {"contradicted", "missing"}:
        verdict = "needs_work"
    elif "uncertain" in relations:
        verdict = "insufficient_evidence"
    elif not draft["complete"] or not audit["complete"]:
        verdict = "needs_work"
    else:
        verdict = "satisfactory"
    priority = {"contradicted": 0, "missing": 1, "uncertain": 2, "supported": 3}
    selected = {}
    # Audit wins equal-priority ties. Positive checks cannot erase remaining negatives.
    effective = [uncertain[c["learner_quote"]] if c["learner_quote"] in uncertain and c["relation"] in {"contradicted", "missing"} else c for c in audit["checks"] + draft["checks"]]
    for check in sorted(effective, key=lambda c: priority[c["relation"]]):
        selected.setdefault(check["learner_quote"], {k: v for k, v in check.items() if k != "evidence_quote"})
    checks = list(selected.values())[:6]
    labels = {"supported": "本点有资料支持", "contradicted": "与资料矛盾，需要修正", "missing": "要点未回答完整", "uncertain": "证据不足，暂不判定"}
    heading = {"satisfactory": "参考核对未发现明显问题，不代表已经掌握。", "needs_work": "参考核对发现错误或遗漏，仍需修正。", "insufficient_evidence": "证据不足，暂不作正确性评价。"}[verdict]
    text = heading + "\n" + "\n".join(f"学生原句：{c['learner_quote']}\n核对：{labels[c['relation']]}。{c['explanation']}" for c in checks)
    return {"evaluation": verdict, "feedback": text, "citation_ids": list(dict.fromkeys(cid for c in checks for cid in c["citation_ids"])), "feedback_checks": checks, "grading_version": GRADING_VERSION}
