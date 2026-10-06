"""Public demo/evaluation utilities never touch private configuration or learner records."""

import hashlib
import json

import pytest


def test_demo_preparation_manifest_and_refuses_existing_data(tmp_path):
    from knowledge_base_extension.demo import prepare

    output = tmp_path / "dedicated"
    manifest = prepare(output)
    assert manifest["dataset"] == "synthetic-redis-v1"
    assert manifest["acceptance_executor"] == "user_or_explicitly_authorized_test_agent"
    assert set(manifest["deliberately_untrusted"]) == {"conflict.md", "injection.md"}
    assert "versioned-update.txt" not in manifest["initial_import"]
    assert {"rdb.md", "aof.md", "types.md", "redis.pdf"} <= set(manifest["initial_import"])
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    before = (output / "types.md").read_bytes()
    with pytest.raises(ValueError, match="empty"):
        prepare(output)
    assert (output / "types.md").read_bytes() == before
    with pytest.raises(ValueError, match="absolute"):
        prepare("relative-path")


def test_evaluation_review_keeps_missing_failed_and_human_fields_distinct():
    from knowledge_base_extension.evaluation_review import summarize

    results = {
        "summary": {"dataset": "synthetic-redis-v1"},
        "cases": [
            {"case_id": "q01", "kind": "single", "answer": "synthetic answer"},
            {"case_id": "q19", "kind": "no_answer", "answer": "insufficient"},
            {"case_id": "q18", "kind": "conflict", "answer": None},
        ],
    }
    review = {
        "dataset": "synthetic-redis-v1",
        "reviewer_kind": "developer_preliminary",
        "reviewer": "developer",
        "cases": [
            {"case_id": "q01", "answer_correct": True, "citation_supported": False, "no_answer_handled": None, "conflict_handled": None, "reason": "unsupported clause"},
        ],
    }
    report = summarize(results, review)
    assert report["scored_answers"] == 1 and report["unreviewed_cases"] == ["q19", "q18"]
    assert report["answer_correctness"] == 1 and report["citation_support"] == 0
    assert report["human_verified"] is False and report["no_answer_handling"] is None
    assert report["failed_or_missing_answers"] == ["q18"]
    agent_review = {**review, "reviewer_kind": "agent", "reviewer": "authorized synthetic test agent"}
    assert summarize(results, agent_review)["human_verified"] is False
    assert summarize(results, agent_review)["reviewer_kind"] == "agent"
    invalid = json.loads(json.dumps(review))
    invalid["cases"][0]["answer_correct"] = "yes"
    with pytest.raises(ValueError):
        summarize(results, invalid)
    invalid = json.loads(json.dumps(review))
    invalid["cases"].append(dict(invalid["cases"][0]))
    with pytest.raises(ValueError):
        summarize(results, invalid)
    invalid = json.loads(json.dumps(review))
    invalid["cases"][0]["case_id"] = "q18"
    with pytest.raises(ValueError, match="answer"):
        summarize(results, invalid)


def test_closed_store_backup_restore_includes_objects_and_index(tmp_path):
    """Quiescent synthetic store snapshot, not a copy of a running production database."""
    import shutil

    from knowledge_base_extension.evaluate import OfflineConceptEmbedding
    from knowledge_base_extension.rag import RAGConfig, RAGIndex
    from knowledge_base_extension.store import KnowledgeStore

    root = tmp_path / "source"
    store = KnowledgeStore(root)
    base = store.create("synthetic", "Synthetic backup")["knowledge_base_id"]
    doc = store.import_file("synthetic", base, "rdb.md", b"RDB saves a snapshot.", {"type": "synthetic"})
    rag = RAGIndex(store, RAGConfig(), OfflineConceptEmbedding())
    rag.index_document("synthetic", doc["document_id"])
    reference = rag.search("synthetic", [base], "RDB snapshot")["evidence"][0]["citation_id"]
    # No service/workers are active; all store transaction connections have closed.
    shutil.copytree(root, tmp_path / "backup")
    shutil.copytree(tmp_path / "backup", tmp_path / "restored")
    restored = KnowledgeStore(tmp_path / "restored")
    with restored.transaction() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reopened = RAGIndex(restored, RAGConfig(), OfflineConceptEmbedding())
    assert reopened.citation("synthetic", reference)
    assert reopened.search("synthetic", [base], "RDB snapshot")["evidence"]
    for source in (root / "objects").rglob("*"):
        if source.is_file():
            assert source.read_bytes() == (tmp_path / "restored" / source.relative_to(root)).read_bytes()


def test_recorded_answers_require_explicit_opt_in_and_do_not_auto_grade(tmp_path):
    from knowledge_base_extension.evaluate import benchmark

    calls = []

    def answerer(rag, owner, bases, case):
        evidence = rag.search(owner, bases, case["query"])["evidence"]
        if case["id"] == "q23":
            assert any("[VERSION-2]" in item["text"] for item in evidence)
            assert not any(item["document_name"] == "versioned.txt" and "[VERSION-1]" in item["text"] for item in evidence)
        calls.append(case["id"])
        return {"answer": "Controlled fixture answer", "citation_ids": [], "model_usage": {"input_tokens": 12, "output_tokens": 3}}

    with pytest.raises(ValueError, match="opt-in"):
        benchmark(tmp_path / "refused", answerer=answerer)
    summary = benchmark(tmp_path / "run", answerer=answerer, allow_service_calls=True, answer_case_ids=["q01", "q23", "q24"])
    assert calls == ["q01", "q23", "q24"]
    assert summary["recorded_answers"] == 3 and summary["answer_correctness"] is None
    report = json.loads((tmp_path / "run" / "results.json").read_text(encoding="utf-8"))
    assert report["cases"][0]["answer"] == "Controlled fixture answer"
    assert all(case["reviewer"] is None for case in report["cases"])
    assert report["cases"][1]["answer"] is None


def test_recorded_answer_bound_uses_utf8_and_retains_failure_case(tmp_path):
    from knowledge_base_extension.evaluate import benchmark

    def answerer(rag, owner, bases, case):
        return {"answer": "合" * (12000 if case["id"] == "q01" else 17000)}

    summary = benchmark(tmp_path, answerer=answerer, allow_service_calls=True, answer_case_ids=["q01", "q02"])
    assert summary["recorded_answers"] == 1 and summary["failed_answers"] == 1
    cases = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["cases"]
    assert cases[0]["answer"] and cases[1]["answer"] is None
    assert cases[1]["answer_error_type"] == "ValueError"
