"""C 组采集：cheap 模型 + 精简提示词，逐题建会话、提问、原子落盘、逐题检查点。

设计要点（与 ECS 独立测评要求对齐）：
1. 每题一个全新会话，互不污染；
2. 所有写入都是「临时文件 + os.replace」原子替换，断电/被杀不会留半截文件；
3. 每题结束立即向 checkpoint.jsonl 追加一行并 fsync，重跑时据此跳过已完成题；
4. 发送状态不明（请求可能已到达上游但没拿到响应）记为 UNKNOWN，**不自动重发**，
   并在后续续跑中默认继续跳过，除非显式传 --allow-unknown-retry；
5. 每次尝试的原始 SSE 单独留档，try 次数与用量逐次记账，不把「重试成功」混成「一次成功」；
6. 后台线程定期采样 CPU / RSS / cgroup 内存 / OOM / 磁盘 / iowait，落 resources.jsonl。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import httpx

# 这些状态是「传输/上游拒绝」，请求本身没有产生有效生成，可以安全重试。
RETRYABLE = {"EMPTY_ANSWER", "TRUNCATED", "TOO_SHORT", "PROVIDER_REJECTED"}
# 这些状态不计入「可重试」，也不得自动重发。
TERMINAL = {"COMPLETED", "UNKNOWN", "ID_REUSE_DENIED", "PROVIDER_DENIED"}
# 明确「没发出去」的异常：连接都没建立，可以重试。
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再原子替换，避免进程被杀时留下半个文件。"""
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict) -> None:
    """追加一行检查点并落盘。单行 O_APPEND 写 + fsync，避免并发/断电丢行。"""
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_checkpoint(path: Path) -> dict[int, dict]:
    """读出每个题号的最后一次检查点（后写覆盖先写）。"""
    state: dict[int, dict] = {}
    if not path.exists():
        return state
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row.get("ordinal"), int):
            state[row["ordinal"]] = row
    return state


def parse_sse(text: str) -> list[dict]:
    """把 SSE 文本切成帧列表；坏帧单独标记而不是整题丢弃。"""
    frames = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        payload = "\n".join(line[6:] for line in block.splitlines() if line.startswith("data: "))
        if not payload:
            continue
        try:
            frames.append(json.loads(payload))
        except json.JSONDecodeError:
            frames.append({"response_type": "_unparsed", "raw": payload})
    return frames


def answer_of(frames: list[dict]) -> str:
    """拼接所有 answer 帧的正文。"""
    return "".join(f.get("content", "") for f in frames if f.get("response_type") == "answer")


def terminal_of(frames: list[dict]) -> dict:
    """取最后一帧 complete 的 data 作为终态描述。"""
    for frame in reversed(frames):
        if frame.get("response_type") == "complete":
            return frame.get("data") or {}
    return {}


def error_of(frames: list[dict]) -> dict:
    """取第一个上游错误帧的错误码与阶段。

    上游故障（例如 SiliconFlow 503 System is too busy）会以 response_type=error
    的帧结束流，此时正文必然为空。若不单独提取，就会被「空答案」判定吞掉，
    把服务端故障误记成模型质量差，进而误导重试与结论。
    """
    for frame in frames:
        if frame.get("response_type") == "error":
            data = frame.get("data") or {}
            return {"code": data.get("error_code") or frame.get("content") or "",
                    "stage": data.get("stage"), "role": data.get("role")}
    return {}


class ResourceSampler(threading.Thread):
    """后台采样资源占用，只记录观测值，不改变任何限额。"""

    def __init__(self, out_dir: Path, interval: float = 5.0):
        super().__init__(daemon=True)
        self.out_dir = out_dir
        self.interval = interval
        self.stop_event = threading.Event()
        self.peak = {"rss_peak_kb": 0, "cgroup_memory_current": 0, "cgroup_memory_peak": 0,
                     "oom_kill": 0, "disk_free_min_bytes": 0}
        self.samples = 0

    @staticmethod
    def _read(path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _cgroup_number(self, name: str) -> int:
        raw = self._read(f"/sys/fs/cgroup/{name}").strip()
        if not raw or raw == "max":
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    def _oom_kills(self) -> int:
        for line in self._read("/sys/fs/cgroup/memory.events").splitlines():
            if line.startswith("oom_kill "):
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError):
                    return 0
        return 0

    def _rss_kb(self) -> int:
        for line in self._read("/proc/self/status").splitlines():
            if line.startswith("VmHWM:"):
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError):
                    return 0
        return 0

    def _disk_free(self) -> int:
        try:
            # shutil 在 Linux/Windows 都有实现，容器内读的就是输出目录所在文件系统。
            return shutil.disk_usage(self.out_dir).free
        except OSError:
            return 0

    def _iowait(self) -> int:
        fields = self._read("/proc/stat").splitlines()
        if not fields or not fields[0].startswith("cpu "):
            return 0
        parts = fields[0].split()
        try:
            return int(parts[5])
        except (IndexError, ValueError):
            return 0

    def sample(self) -> dict:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rss_peak_kb": self._rss_kb(),
            "cgroup_memory_current": self._cgroup_number("memory.current"),
            "cgroup_memory_peak": self._cgroup_number("memory.peak"),
            "cgroup_memory_max": self._cgroup_number("memory.max"),
            "cgroup_cpu_max": self._read("/sys/fs/cgroup/cpu.max").strip(),
            "oom_kill": self._oom_kills(),
            "disk_free_bytes": self._disk_free(),
            "iowait_jiffies": self._iowait(),
        }
        for key in ("rss_peak_kb", "cgroup_memory_current", "cgroup_memory_peak", "oom_kill"):
            self.peak[key] = max(self.peak[key], row[key])
        if row["disk_free_bytes"]:
            free = row["disk_free_bytes"]
            self.peak["disk_free_min_bytes"] = (min(self.peak["disk_free_min_bytes"], free)
                                                if self.peak["disk_free_min_bytes"] else free)
        self.samples += 1
        return row

    def run(self) -> None:
        path = self.out_dir / "resources.jsonl"
        while not self.stop_event.is_set():
            try:
                append_jsonl(path, self.sample())
            except OSError:
                pass
            self.stop_event.wait(self.interval)


def classify(record: dict, min_chars: int) -> str:
    """根据 HTTP 状态、上游错误帧与解析结果判定本次尝试的性质。"""
    status_code = record.get("http_status")
    if isinstance(status_code, int):
        # 401/402/403 是账号或鉴权问题，重试一百次也一样，直接判死。
        if status_code in (401, 402, 403):
            return "PROVIDER_DENIED"
        if status_code >= 500 or status_code == 429:
            return "PROVIDER_REJECTED"
    # 上游错误帧优先于正文判定：这类尝试正文为空，但原因在服务端而非模型。
    if (record.get("provider_error") or {}).get("code"):
        return "PROVIDER_REJECTED"
    answer = record.get("answer") or ""
    if not record.get("terminal"):
        return "TRUNCATED" if answer else "EMPTY_ANSWER"
    if not answer:
        return "EMPTY_ANSWER"
    if len(answer) < min_chars:
        return "TOO_SHORT"
    return "COMPLETED"


def collect_one(client: httpx.Client, case: dict, out_dir: Path, tag: str, *, attempt: int = 1,
                min_chars: int = 20) -> dict:
    """单题单次尝试：建会话 -> 写设置 -> 提问 -> 原子落盘。"""
    ordinal = case["ordinal"]
    started = time.monotonic()
    request_id = str(uuid.uuid4())
    record = {"ordinal": ordinal, "case_id": case["case_id"], "dataset": case["dataset"],
              "question": case["question"], "tag": tag, "attempt": attempt,
              "request_id": request_id, "status": "FAILED"}
    session_id = None
    send_state = "not_sent"
    try:
        scope = {"knowledge_base_scope": case["knowledge_base_ids"],
                 "document_scope": case["document_ids"]}
        generation = {"reasoning_mode": "quick", "max_output_tokens": 2048}

        response = client.post("/api/v1/sessions", json={
            "title": f"{tag}-{ordinal:02d} {case['case_id']}",
            **scope, **generation,
            "source_evidence_mode": "bounded_source_v1",
            "retrieval_strategy_mode": "query_structure_v1"})
        response.raise_for_status()
        session_id = response.json()["data"]["id"]
        record["session_id"] = session_id

        client.put(f"/api/v1/sessions/{session_id}/generation-settings", json=generation).raise_for_status()

        body = {"content": case["question"], "request_id": request_id,
                "mode": "quick", **scope, **generation, "reasoning_effort": "off"}

        chunks: list[bytes] = []
        with client.stream("POST", f"/api/v1/sessions/{session_id}/messages", json=body) as stream:
            # 拿到响应头就说明请求确实已经发到上游，之后的中断属「已发送、响应不完整」。
            record["http_status"] = stream.status_code
            send_state = "sent"
            for chunk in stream.iter_bytes():
                chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        atomic_write_text(out_dir / f"{ordinal:02d}.a{attempt}.sse", raw)

        frames = parse_sse(raw)
        record["frames"] = frames
        record["answer"] = answer_of(frames).strip()
        record["terminal"] = terminal_of(frames)
        record["provider_error"] = error_of(frames)
        record["answer_len"] = len(record["answer"])
        record["status"] = classify(record, min_chars)

        # 回读历史，取服务端持久化的最终答案与引用，便于交叉校验。
        history = client.get(f"/api/v1/sessions/{session_id}/messages")
        if history.status_code == 200:
            assistants = [m for m in history.json()["data"] if m.get("role") == "assistant"]
            if assistants:
                record["history_answer"] = assistants[-1].get("content", "")
                record["history_usage"] = assistants[-1].get("usage")
                record["references"] = assistants[-1].get("knowledge_references") or []
                record["is_completed"] = assistants[-1].get("is_completed")
    except Exception as error:  # 采集失败要保留现场，不能吞掉
        record["error"] = {"type": type(error).__name__, "message": str(error)[:500]}
        if isinstance(error, NOT_SENT_ERRORS):
            # 连接都没建立：确定没发出去，可安全重试。
            send_state = "not_sent"
            record["status"] = "CONNECT_FAILED"
        elif send_state == "sent" and record.get("http_status") is not None:
            # 已经拿到响应头，属于响应不完整，可重试。
            record["status"] = "TRUNCATED"
        else:
            # 请求可能已到达上游但没拿到任何响应：发送状态不明，只能记 UNKNOWN。
            send_state = "unknown"
            record["status"] = "UNKNOWN"
        if "MODEL_CALL_ID_REUSE_DENIED" in str(error):
            record["status"] = "ID_REUSE_DENIED"
    finally:
        record["send_state"] = send_state
        record["elapsed_seconds"] = round(time.monotonic() - started, 1)
        record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        blob = json.dumps(record, ensure_ascii=False, indent=1)
        record["sha256"] = sha256_text(blob)
        ok = record["status"] == "COMPLETED"
        # 正式结果与尝试留档都原子写入；正式文件只在完成时出现，重试时不会被跳过。
        atomic_write_text(out_dir / f"{ordinal:02d}.a{attempt}.json", blob)
        if ok:
            atomic_write_text(out_dir / f"{ordinal:02d}.json", blob)
    return {"ordinal": ordinal, "status": record["status"], "ok": ok, "send_state": send_state,
            "answer_len": record.get("answer_len", 0), "seconds": record["elapsed_seconds"],
            "attempt": attempt, "sha256": record["sha256"],
            "retryable": record["status"] in RETRYABLE}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", default="/run/secrets/api_token")
    parser.add_argument("--ordinals", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-sleep", type=float, default=20.0)
    parser.add_argument("--min-answer-chars", type=int, default=20)
    parser.add_argument("--allow-unknown-retry", action="store_true",
                        help="允许对上次记账为 UNKNOWN 的题重发；默认不重发")
    parser.add_argument("--resume-rounds", type=int, default=1,
                        help="本轮内对可重试题目的复跑轮数（UNKNOWN 永远不复跑）")
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    wanted = {int(x) for x in args.ordinals.split(",") if x.strip()} if args.ordinals else set()
    if wanted:
        cases = [c for c in cases if c["ordinal"] in wanted]
    cases.sort(key=lambda c: c["ordinal"])
    if args.limit:
        cases = cases[: args.limit]

    args.out.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.out / "checkpoint.jsonl"
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    headers = {"Authorization": "Bearer " + token, "Origin": "http://127.0.0.1:18880"}

    sampler = ResourceSampler(args.out)
    sampler.start()
    attempted_calls = 0
    results: list[dict] = []

    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers,
                      timeout=httpx.Timeout(900.0, connect=10.0)) as client:
        for round_index in range(max(1, args.resume_rounds)):
            # 每轮重新读检查点，避免用上一轮的旧状态做判定。
            prior = read_checkpoint(checkpoint_path)
            pending: list[int] = []
            for case in cases:
                ordinal = case["ordinal"]
                if (args.out / f"{ordinal:02d}.json").exists():
                    if not any(r.get("ordinal") == ordinal and r.get("ok") for r in results):
                        results.append({"ordinal": ordinal, "status": "SKIPPED_EXISTING", "ok": True})
                    continue
                previous = prior.get(ordinal) or {}
                if previous.get("status") == "UNKNOWN" and not args.allow_unknown_retry:
                    # 发送状态不明的题一律不自动重发，等人工确认后再决定。
                    results.append({"ordinal": ordinal, "status": "UNKNOWN_HELD", "ok": False})
                    continue
                if round_index > 0 and previous.get("status") not in RETRYABLE:
                    results.append({"ordinal": ordinal, "status": "HELD", "ok": False})
                    continue

                result = {"ordinal": ordinal, "status": "NOT_RUN", "ok": False}
                for attempt in range(1, args.retries + 2):
                    result = collect_one(client, case, args.out, args.tag, attempt=attempt,
                                         min_chars=args.min_answer_chars)
                    attempted_calls += 1
                    append_jsonl(checkpoint_path, {**result, "tag": args.tag,
                                                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
                    if result.get("ok") or result["status"] in TERMINAL:
                        break
                    print(json.dumps({"retry": result, "attempt": attempt}, ensure_ascii=False), flush=True)
                    # 上游容量类故障（503/429）退避要更久：否则只是在同一个拥塞窗口里空转，
                    # 既拿不到答案，还会持续占用有限的重试次数。
                    factor = 3 if result["status"] == "PROVIDER_REJECTED" else 1
                    time.sleep(min(300, args.retry_sleep * attempt * factor))
                print(json.dumps(result, ensure_ascii=False), flush=True)
                results.append(result)
                if not result.get("ok") and result["status"] not in TERMINAL:
                    pending.append(ordinal)
            if not pending:
                break
            open_ordinals = pending

    sampler.stop_event.set()
    sampler.join(timeout=5)
    final = sampler.sample()

    state = read_checkpoint(checkpoint_path)
    completed = sorted(int(p.stem) for p in args.out.glob("[0-9][0-9].json"))
    status_of = {int(k): v.get("status") for k, v in state.items()}
    unknown = sorted(o for o, s in status_of.items() if s == "UNKNOWN")
    failed = sorted(o for o, s in status_of.items()
                    if s not in ("COMPLETED",) and o not in completed and o not in unknown)
    not_started = sorted(c["ordinal"] for c in cases
                         if c["ordinal"] not in status_of and c["ordinal"] not in completed)

    manifest = {p.name: sha256_text(p.read_text(encoding="utf-8"))
                for p in sorted(args.out.glob("[0-9][0-9].json"))}
    summary = {
        "tag": args.tag,
        "scope": {"total": len(cases), "ordinals": [c["ordinal"] for c in cases]},
        "counts": {"completed": len(completed), "unknown": len(unknown),
                   "failed": len(failed), "not_started": len(not_started)},
        "completed_ordinals": completed,
        "unknown_ordinals": unknown,
        "failed_ordinals": failed,
        "not_started_ordinals": not_started,
        "last_completed_ordinal": completed[-1] if completed else None,
        "provider_calls_this_run": attempted_calls,
        "retried_ordinals": sorted({r["ordinal"] for r in results if r.get("attempt", 1) > 1}),
        "resources": {**sampler.peak, "samples": sampler.samples, "final": final},
        "output_dir": str(args.out),
        "manifest": manifest,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    blob = json.dumps(summary, ensure_ascii=False, indent=1)
    atomic_write_text(args.out / "_SUMMARY.json", blob)
    # DONE 里带上摘要的哈希，读数据前可先用它校验摘要本身没被截断。
    atomic_write_text(args.out / "DONE",
                      f"finished_at={summary['finished_at']}\n"
                      f"completed={len(completed)}/{len(cases)}\n"
                      f"unknown={len(unknown)} failed={len(failed)} not_started={len(not_started)}\n"
                      f"summary_sha256={sha256_text(blob)}\n")
    print(json.dumps({"SUMMARY": {k: summary[k] for k in
                                  ("tag", "counts", "last_completed_ordinal",
                                   "provider_calls_this_run")}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
