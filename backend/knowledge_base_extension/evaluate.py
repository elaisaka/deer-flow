"""Explicit synthetic benchmark. Offline doubles measure plumbing, not semantics."""

import argparse
import json
import statistics
from pathlib import Path

from .rag import EmbeddingClient, RAGConfig, RAGIndex
from .store import KnowledgeStore

FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "rag-fixtures"


class OfflineConceptEmbedding:
    """Only this evaluation CLI uses a declared, controllable test double."""

    model = "offline-concept-fixture-v1"
    identity = "offline-concept-fixture-v1-not-semantic-service"
    concepts = (
        ("rdb", "快照", "snapshot", "后台保存", "写时复制", "bgsave", "save", "fork"),
        ("aof", "追加日志", "appendfsync", "刷盘", "everysec", "每秒同步", "always"),
        ("恢复", "备份", "演练", "启用"),
        ("ttl", "expire", "过期", "set", "get", "字符串"),
        ("sorted set", "集合", "排行榜", "zadd", "分数"),
        ("零丢失", "冲突", "故障", "丢失"),
        ("间隔", "天", "策略文档"),
    )
    dimension = len(concepts) + 1

    def embed(self, texts, **kwargs):
        return [[float(any(term in text.lower() for term in terms)) for terms in self.concepts] + [0.0001] for text in texts]


def benchmark(output, *, mode="offline", embedding_config=None, allow_service_calls=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Use an empty dedicated evaluation output directory")
    if mode == "real":
        if not allow_service_calls or not embedding_config:
            raise ValueError("Real mode requires private embedding config and explicit --allow-service-calls")
        embedder = EmbeddingClient(json.loads(Path(embedding_config).read_text(encoding="utf-8")))
        if not embedder.configured:
            raise ValueError("Embedding service not configured")
    else:
        embedder = OfflineConceptEmbedding()
    config = RAGConfig()
    store = KnowledgeStore(output / "store")
    owner = "synthetic-evaluator"
    base = store.create(owner, "Synthetic Redis evaluation")["knowledge_base_id"]
    documents = {}
    for source in sorted(FIXTURES.iterdir()):
        if source.suffix not in {".md", ".txt"} or source.name == "versioned-update.txt":
            continue
        doc = store.import_file(owner, base, source.name, source.read_bytes(), {"type": "synthetic", "fixture": source.name})
        documents[source.name] = doc
    rag = RAGIndex(store, config, embedder)
    for doc in documents.values():
        rag.index_document(owner, doc["document_id"])
    dataset = json.loads((FIXTURES / "questions.json").read_text(encoding="utf-8"))
    results, recalls, hits, durations, no_answer = [], [], [], [], []
    old_references = []
    for case in dataset["cases"]:
        if case["kind"] == "delete":
            doc = documents["versioned.txt"]
            preview = store.deletion_preview(owner, "document", doc["document_id"])
            store.delete(owner, "document", doc["document_id"], preview["digest"])
        result = rag.search(owner, [base], case["query"])
        durations.append(result["elapsed_ms"])
        matched = [any(e["document_name"] == filename and f"[{marker}]" in e["text"] for e in result["evidence"]) for filename, marker in case["expected"]]
        recall = sum(matched) / len(matched) if matched else None
        if recall is not None:
            recalls.append(recall)
            hits.append(any(matched))
        if case["kind"] == "no_answer":
            no_answer.append(not result["evidence"])
        sample = {"case_id": case["id"], "kind": case["kind"], "recall_at_k": recall, "retrieval": result, "answer": None, "answer_correct": None, "citation_supported": None, "no_answer_handled": None, "reviewer": None}
        if case["kind"] == "update":
            doc = documents["versioned.txt"]
            old_references = [e["citation_id"] for e in result["evidence"] if e["document_id"] == doc["document_id"]]
            updated = store.import_file(owner, base, "versioned.txt", (FIXTURES / "versioned-update.txt").read_bytes(), {"type": "synthetic"}, document_id=doc["document_id"], expected_revision=1)
            try:
                rag.search(owner, [base], case["query"])
                unavailable = False
            except Exception as error:
                unavailable = str(error) == "index_not_ready"
            rag.index_document(owner, doc["document_id"])
            after = rag.search(owner, [base], case["query"])
            sample["update"] = {
                "new_version_waited_for_index": unavailable,
                "only_current_version": all(e["version_id"] == updated["version_id"] for e in after["evidence"] if e["document_id"] == doc["document_id"]),
                "expected_new_evidence_hit": any(e["document_id"] == doc["document_id"] and "[VERSION-2]" in e["text"] for e in after["evidence"]),
            }
        if case["kind"] == "delete":
            doc = documents["versioned.txt"]
            sample["delete"] = {"deleted_doc_absent": all(e["document_id"] != doc["document_id"] for e in result["evidence"]), "old_citations_unavailable": []}
            for citation in old_references:
                try:
                    rag.citation(owner, citation)
                    sample["delete"]["old_citations_unavailable"].append(False)
                except Exception as error:
                    sample["delete"]["old_citations_unavailable"].append(str(error) == "source_unavailable")
        results.append(sample)
    summary = {
        "mode": mode,
        "dataset": dataset["version"],
        "embedding_model": embedder.model,
        "embedding_dimension": embedder.dimension,
        "splitter": config.splitter_version,
        "chunk_chars": config.chunk_chars,
        "overlap": config.overlap,
        "top_k": config.top_k,
        "min_score": config.min_score,
        "context_chars": config.context_chars,
        "cases": len(results),
        "answerable_cases": len(recalls),
        "macro_recall_at_k": statistics.mean(recalls),
        "any_expected_evidence_hit_rate": statistics.mean(hits),
        "no_answer_cases": len(no_answer),
        "no_answer_zero_candidate_rate": statistics.mean(no_answer),
        "retrieval_ms_median": statistics.median(durations),
        "retrieval_ms_max": max(durations),
        "answer_correctness": None,
        "citation_support": None,
        "no_answer_response_handling": None,
        "service_cost": None,
        "limitations": "Offline mode validates plumbing with a test double; it is not real embedding quality. Zero candidates is not answer refusal. "
        "Answers/citation support need recorded chat answers and human evidence review. Real costs are not inferred from token guesses.",
    }
    (output / "results.json").write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--allow-service-calls", action="store_true", help="Send only these synthetic documents and fixed questions to the configured service; remote providers may charge")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.output, mode=args.mode, embedding_config=args.embedding_config, allow_service_calls=args.allow_service_calls), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
