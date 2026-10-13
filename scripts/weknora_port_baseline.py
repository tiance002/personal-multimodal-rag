"""Read-only Git/source baseline capture. No network, model or database writes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

UPSTREAM = "3e8b0bfc80b845b2d4b2ed683994748741450a97"
BASELINE = "55372f7bae8e30a8150d16e0eb12262a038cbea2"
SOURCE_PATHS = [
    "config/config.yaml", "internal/types/retrieval_config.go", "internal/types/model.go",
    "internal/application/service/session_knowledge_qa.go",
    "internal/models/api/openaicompletions/request.go", "internal/models/api/openaicompletions/client.go",
    "internal/models/api/transport.go", "internal/models/api/usage.go", "internal/models/api/sse.go",
    "internal/models/api/stream_assembler.go", "internal/models/api/options.go",
    "internal/models/providers/siliconflow.go", "internal/models/providers/deepseek.go",
    "internal/infrastructure/chunker/strategy.go", "internal/infrastructure/chunker/splitter.go",
    "internal/application/service/knowledge_process.go", "internal/application/service/knowledge_process_config.go",
    "internal/application/service/retriever/keywords_vector_hybrid_indexer.go",
    "internal/application/repository/retriever/postgres/repository.go",
    "internal/application/service/knowledgebase_search_fusion.go",
    "internal/modelcontext/citations.go", "internal/stream/redis_manager.go",
    "internal/handler/session/stream.go", "frontend/src/composables/useChatStreamHandler.ts",
    "frontend/src/views/chat/components/RagPipelineProgress.vue", "LICENSE", "THIRD_PARTY_NOTICES.md",
]
SOURCE_PATHS += ["internal/application/service/chat_pipeline/" + n + ".go" for n in (
    "query_understand", "search", "search_parallel", "query_expansion", "rerank", "merge", "merge_expand",
    "merge_history", "filter_top_k", "into_chat_message", "load_history", "memory_recall", "chat_completion_stream",
    "progress", "references")]
SOURCE_PATHS += ["internal/application/service/memory/" + n + ".go" for n in (
    "service", "extract", "search", "vector", "consolidate", "scope", "lexical")]
SOURCE_PATHS += ["docreader/parser/" + n + ".py" for n in (
    "pdf_parser", "image_parser", "excel_parser", "docx_parser", "registry", "base_parser")]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args]).decode("utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--task-zip", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    if git(args.upstream, "rev-parse", "HEAD") != UPSTREAM:
        raise RuntimeError("PINNED_UPSTREAM_MISMATCH")
    rows, missing = [], []
    for name in SOURCE_PATHS:
        file = args.upstream / name
        if not file.is_file():
            missing.append(name)
            continue
        data = file.read_bytes()
        object_data = subprocess.check_output(["git", "-C", str(args.upstream), "show", "HEAD:" + name])
        blob = hashlib.sha1(b"blob " + str(len(object_data)).encode() + b"\0" + object_data).hexdigest()
        expected = git(args.upstream, "rev-parse", "HEAD:" + name)
        working_blob = git(args.upstream, "hash-object", "--path=" + name, str(file))
        if blob != expected or working_blob != expected:
            raise RuntimeError("UPSTREAM_BLOB_MISMATCH:" + name)
        # Source availability is not a behavioral parity claim.
        rows.append({"path": name, "blob": blob, "sha256": hashlib.sha256(data).hexdigest(),
                     "bytes": len(data), "status": "BLOB_VERIFIED"})
    task = root / "docs/weknora-port/task"
    task.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(args.task_zip) as archive:
        for member in archive.infolist():
            if member.filename.startswith(("01_", "02_", "03_", "README_")) and member.filename.endswith(".md"):
                if Path(member.filename).name != member.filename:
                    raise RuntimeError("INVALID_TASK_MEMBER")
                (task / member.filename).write_bytes(archive.read(member))
                names.append(member.filename)
    if len(names) != 4:
        raise RuntimeError("TASK_PACKAGE_INCOMPLETE")
    config = (args.upstream / "config/config.yaml").read_text(encoding="utf-8")
    observed = {}
    for name in ("max_rounds", "keyword_threshold", "embedding_top_k", "vector_threshold", "rerank_threshold",
                 "rerank_top_k", "enable_rewrite", "enable_query_expansion", "enable_rerank", "max_completion_tokens"):
        match = re.search(r"^\s+" + name + r":\s+([^#\n]+)", config, re.M)
        observed[name] = match.group(1).strip() if match else "NOT_FOUND"
    expected_cases = {"schema_version": "weknora-source-cases/v1", "upstream": UPSTREAM,
        "yaml_observed": observed,
        "retrieval_type_fallbacks": {"embedding_top_k": 50, "vector_threshold": 0.15,
            "keyword_threshold": 0.3, "rerank_top_k": 10, "rerank_threshold_nil": 0.2,
            "rerank_threshold_non_nil_zero": 0, "rrf_k": 60, "rrf_weights": [0.7, 0.3]},
        "transport_fallback_seconds": {"chat": 300, "stream": 600, "existing_deadline": "PRESERVED"},
        "status": "SOURCE_EXPECTATIONS_ONLY_NOT_BEHAVIORALLY_VERIFIED"}
    fixtures = root / "tests/weknora_port/fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "expected_behavior_cases.json").write_text(json.dumps(expected_cases, indent=2) + "\n")
    # Freeze observable owner algorithm from the exact local baseline for later P5 replay.
    from backend.app.application.rule_router_v1 import RouterPolicy, decide
    questions = ["缓存是什么？", "列出三个步骤", "比较两个方案的差异", "若金额≤100且审批通过是否可以报销？",
                 "总结后比较，最后制定计划", "解释文档内容"]
    decisions = []
    for question in questions:
        for mode in ("CHEAP_ONLY", "EXPENSIVE_ONLY", "DYNAMIC"):
            result = decide(question, "Fixed synthetic context", RouterPolicy(mode=mode))
            decisions.append({"question": question, "mode": mode, "decision": result.public(RouterPolicy(mode=mode))})
    (fixtures / "p5_legacy_decisions.json").write_text(json.dumps(
        {"baseline": BASELINE, "execution": "LOCAL_PURE_FUNCTION", "cases": decisions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"schema_version": "weknora-port-baseline/v1", "worktree_head": git(root, "rev-parse", "HEAD"),
        "branch": git(root, "branch", "--show-current"), "upstream": UPSTREAM, "sources": rows,
        "missing_requested_sources": missing, "task_zip_sha256": hashlib.sha256(args.task_zip.read_bytes()).hexdigest(),
        "provider_calls": 0, "old_db_writes": 0, "runtime_identity": "NOT_VERIFIED_DOCKER_UNAVAILABLE"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SOURCE_IDENTITIES_CAPTURED", "verified_blobs": len(rows),
                      "missing_paths": missing, "provider_calls": 0, "old_db_writes": 0}))


if __name__ == "__main__":
    main()
