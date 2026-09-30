"""Private source-grounded V1 API verification with checkpoints and genuine usage."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def run(endpoint: str, cases_path: Path, materials_path: Path, output: Path) -> int:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 18088:
        raise ValueError("verification endpoint must be isolated loopback port 18088")
    cases_bytes, materials_bytes = cases_path.read_bytes(), materials_path.read_bytes()
    cases = [json.loads(line) for line in cases_bytes.decode("utf-8").splitlines() if line.strip()]
    materials = json.loads(materials_bytes)
    if not 30 <= len(cases) <= 50 or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("expected fixed unique 30–50 cases")
    chunks = {str(chunk["id"]): (file, chunk) for file in materials["files"] for chunk in file["chunks"]}
    kb = materials["kb_id"]
    for case in cases:
        if not {"question", "expected_chunk_ids", "answer_points", "kb_scope"} <= case.keys():
            raise ValueError("required frozen schema missing")
        if case["kb_scope"] != [kb] or any(chunk not in chunks for chunk in case["expected_chunk_ids"]):
            raise ValueError("case source identity mismatch")
    output.mkdir(parents=True, exist_ok=False)
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()
    manifest = {"schema_version": "real-material-verification-v1", "git_sha": git("rev-parse", "HEAD"),
        "source_tree_sha": git("rev-parse", "HEAD^{tree}"), "working_patch_sha256": sha(subprocess.check_output(["git", "diff", "HEAD", "--", "backend", "scripts"])),
        "cases_sha256": sha(cases_bytes), "materials_sha256": sha(materials_bytes), "case_count": len(cases),
        "source_hashes": {file["name"]: file["source_sha256"] for file in materials["files"]},
        "generation_input": "question and retrieved source context only; no gold answer_points/expected IDs",
        "model_usage": "API-returned genuine usage; missing NOT_AVAILABLE", "retries": 0,
        "semantic_review": "source-grounded human/agent review separate from lexical point checks",
        "command": ["python", "-m", "scripts.verify_real_materials", "--cases", str(cases_path), "--materials", str(materials_path), "--output", str(output)]}
    with httpx.Client(timeout=10) as model_client:
        manifest["models"] = model_client.get("http://127.0.0.1:11434/api/tags").json()
    with httpx.Client(base_url=endpoint, timeout=30) as source_client:
        for file in materials["files"]:
            document_id = file["receipt"]["document_id"]
            source = source_client.get(f"/api/v1/documents/{document_id}/source")
            source.raise_for_status()
            if sha(source.content) != file["source_sha256"]:
                raise ValueError("source readback hash mismatch")
            indexed = source_client.get(f"/api/v1/documents/{document_id}/chunks").json()["data"]
            if [(str(c["id"]), c["content_sha256"]) for c in indexed] != [(str(c["id"]), c["content_sha256"]) for c in file["chunks"]]:
                raise ValueError("indexed source identity changed")
    save(output / "manifest.json", manifest)
    stop = threading.Event()
    resource_rows = []
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ("total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "available_extended")]
    def sample() -> None:
        while not stop.is_set():
            try:
                value = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"], text=True, timeout=5).strip().split(",")
                sample_row = {"elapsed_seconds": time.perf_counter()-started, "gpu_used_mib": int(value[0]), "gpu_util_percent": int(value[1])}
                if hasattr(ctypes, "windll"):
                    memory = MemoryStatus()
                    memory.length = ctypes.sizeof(memory)
                    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                        sample_row.update(ram_used_mib=(memory.total_physical-memory.available_physical)/1048576,
                                          ram_available_mib=memory.available_physical/1048576)
                resource_rows.append(sample_row)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            stop.wait(1)
    started = time.perf_counter()
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    rows, conversations = [], {}
    try:
        with httpx.Client(base_url=endpoint, timeout=120) as client:
            for case in cases:
                parent = case.get("follow_up_of")
                if parent:
                    conversation = conversations[parent]
                else:
                    response = client.post("/api/v1/conversations", json={"knowledge_base_scope": [kb],
                        "document_scope": case.get("document_scope", []), "title": "V1资料验证 " + case["case_id"]})
                    response.raise_for_status()
                    conversation = response.json()["data"]
                conversations[case["case_id"]] = conversation
                response = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={
                    "content": case["question"], "mode": case.get("mode", "quick"),
                    "expected_knowledge_base_scope": [kb], "expected_document_scope": conversation["document_scope"]})
                response.raise_for_status()
                answer = response.json()["data"]
                metrics = answer["trace"].get("metrics", {})
                selected = set(metrics.get("selected_context_chunk_ids", []))
                expected = set(case["expected_chunk_ids"])
                citations = []
                for label in answer["citations"]:
                    detail = client.get(f"/api/v1/runs/{answer['run_id']}/citations/{label}")
                    detail.raise_for_status()
                    citations.append(detail.json()["data"])
                transcript = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["data"]
                events = client.get(f"/api/v1/runs/{answer['run_id']}/events").text
                row = {"case_id": case["case_id"], "category": case["category"], "conversation_id": conversation["id"],
                    "result": answer, "citation_details": citations,
                    "expected_context_recall": len(expected & selected)/len(expected) if expected else None,
                    "citation_readback_success": len(citations) == len(answer["citations"]),
                    "citation_active": all(c.get("current_status") == "current" for c in citations),
                    "gold_lexical_points_present": [point in answer["answer"] for point in case["answer_points"]],
                    "lexical_points_basis": "diagnostic only; not semantic accuracy",
                    "refusal_observed": any(term in answer["answer"] for term in ("资料不足", "无法回答", "没有足够", "未提供", "没有提供", "未提及", "not provided", "not contain")) or answer["error_code"] == "NO_CANDIDATES",
                    "expected_refusal": case.get("expected_refusal", False),
                    "history_readback": any(message.get("run_id") == answer["run_id"] for message in transcript) if answer["error_code"] is None else all(message.get("run_id") != answer["run_id"] for message in transcript),
                    "metrics_sse_before_terminal": "event: run.metrics" in events and events.index("event: run.metrics") < max(events.find("event: answer.completed"), events.find("event: run.failed")),
                    "semantic_accuracy": "NOT_EVALUATED", "semantic_citation_support": "NOT_EVALUATED"}
                rows.append(row)
                save(output / (case["case_id"] + ".json"), row)
                print(json.dumps({"case_id": case["case_id"], "completed": len(rows), "total": len(cases), "error": answer["error_code"], "tokens": metrics.get("total_tokens"), "latency_ms": round(metrics.get("total_latency_ms",0))}), flush=True)
        summary = {"status": "COMPLETED", "cases": len(rows), "completed_answers": sum(row["result"]["error_code"] is None for row in rows),
            "generation_calls": sum(sum(call["stage"] == "answer" for call in row["result"]["trace"]["metrics"]["model_calls"]) for row in rows),
            "cloud_calls": sum(row["result"]["trace"]["metrics"]["cloud_called"] for row in rows),
            "citation_readback_pairs": sum(len(row["citation_details"]) for row in rows), "elapsed_seconds": time.perf_counter()-started,
            "gpu_sampled_peak_mib": max((row["gpu_used_mib"] for row in resource_rows),default="NOT_AVAILABLE"),
            "resource_sampling_seconds": 1, "semantic_accuracy": "NOT_EVALUATED", "exit_code": 0}
        save(output / "results.json", {"summary": summary, "per_case": rows})
        with httpx.Client(timeout=10) as model_client:
            models_after = model_client.get("http://127.0.0.1:11434/api/tags").json()
            save(output / "models-after.json", models_after)
            before = {(m["name"], m["digest"]) for m in manifest["models"]["models"]}
            after = {(m["name"], m["digest"]) for m in models_after["models"]}
            if before != after:
                raise ValueError("model digest changed")
        print(json.dumps(summary))
        return 0
    except Exception as exc:
        save(output / "stop.json", {"status": "INTERRUPTED", "completed": len(rows), "error_type": type(exc).__name__, "exit_code": 2})
        print(json.dumps({"status": "INTERRUPTED", "error_type": type(exc).__name__, "completed": len(rows)}))
        return 2
    finally:
        stop.set()
        sampler.join(timeout=6)
        save(output / "resource-samples.json", resource_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:18088")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return run(args.endpoint, args.cases, args.materials, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
