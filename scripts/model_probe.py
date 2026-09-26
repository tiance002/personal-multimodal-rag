from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def run_cli(executable: str, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> tuple[int, float, dict[str, Any] | str]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return response.status, elapsed_ms, json.loads(body)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return 1, elapsed_ms, str(exc)


def get_json(base_url: str, path: str, timeout: float) -> tuple[int, float, dict[str, Any] | str]:
    started = time.perf_counter()
    try:
        with urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as response:
            body = response.read().decode("utf-8")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return response.status, elapsed_ms, json.loads(body)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return 1, elapsed_ms, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("var/reports/m0-model-probe.json"))
    parser.add_argument("--ollama-bin", default=r"E:\ollama\ollama.exe")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--chat-model", default="ornith-1.5:9b")
    parser.add_argument("--embedding-model", default="bge-m3:latest")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    list_exit, list_output = run_cli(args.ollama_bin, "list")
    checks.append({"name": "ollama_list", "exit_code": list_exit, "output": list_output})
    show_chat_exit, show_chat_output = run_cli(args.ollama_bin, "show", args.chat_model)
    checks.append({"name": "ollama_show_chat", "exit_code": show_chat_exit, "output": show_chat_output})
    show_embedding_exit, show_embedding_output = run_cli(args.ollama_bin, "show", args.embedding_model)
    checks.append({"name": "ollama_show_embedding", "exit_code": show_embedding_exit, "output": show_embedding_output})

    version_status, version_latency, version = get_json(args.base_url, "/api/version", timeout=10)
    checks.append({"name": "ollama_version", "http_status": version_status, "latency_ms": version_latency, "response": version})

    chat_status, chat_latency, chat_response = post_json(
        args.base_url,
        "/api/chat",
        {
            "model": args.chat_model,
            "messages": [{"role": "user", "content": "Return JSON with exactly the keys answer and ok. answer must be 1 and ok must be true."}],
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": 64},
        },
        timeout=120,
    )
    chat_content: Any = None
    if isinstance(chat_response, dict):
        message = chat_response.get("message")
        if isinstance(message, dict):
            chat_content = message.get("content")
    chat_content_json: Any = None
    if isinstance(chat_content, str):
        try:
            chat_content_json = json.loads(chat_content)
        except json.JSONDecodeError:
            chat_content_json = chat_content
    checks.append({
        "name": "structured_chat",
        "http_status": chat_status,
        "latency_ms": chat_latency,
        "content": chat_content_json,
        "response": chat_response if chat_status != 200 else {"done_reason": chat_response.get("done_reason") if isinstance(chat_response, dict) else None},
    })

    embedding_status, embedding_latency, embedding_response = post_json(
        args.base_url,
        "/api/embed",
        {"model": args.embedding_model, "input": ["事务回滚"]},
        timeout=120,
    )
    dimensions = None
    if isinstance(embedding_response, dict):
        vectors = embedding_response.get("embeddings")
        if isinstance(vectors, list) and vectors and isinstance(vectors[0], list):
            dimensions = len(vectors[0])
    checks.append({
        "name": "embedding",
        "http_status": embedding_status,
        "latency_ms": embedding_latency,
        "dimensions": dimensions,
        "response": {"model": args.embedding_model} if embedding_status == 200 else embedding_response,
    })

    checks[0]["expected_models_present"] = args.chat_model in list_output and args.embedding_model in list_output
    checks[4]["valid_json"] = isinstance(chat_content_json, dict) and chat_content_json.get("answer") == 1 and chat_content_json.get("ok") is True
    checks[5]["expected_dimensions"] = dimensions == 1024
    passed = all(
        [
            list_exit == 0,
            show_chat_exit == 0,
            show_embedding_exit == 0,
            version_status == 200,
            chat_status == 200 and checks[4]["valid_json"],
            embedding_status == 200 and checks[5]["expected_dimensions"],
            checks[0]["expected_models_present"],
        ]
    )
    report = {
        "probe": "M0 local model capabilities",
        "status": "PASS" if passed else "FAIL",
        "cloud_enabled": False,
        "local_only": True,
        "models": {"chat": args.chat_model, "embedding": args.embedding_model},
        "checks": checks,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
