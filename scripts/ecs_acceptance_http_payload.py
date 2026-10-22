"""Run one frozen ECS HTTP acceptance driver and adapt its saved evidence.

The existing driver owns HTTP, Registry, SendGate and LocalExecution. This
stdlib adapter does not implement requests, grant policy, budgets or retries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

COMPLETE = "ALL_ELIGIBLE_HTTP_COMPLETED_REVIEW_REQUIRED"
_SECRET = re.compile(r"(^|[._-])(env|secret|secrets|credential|credentials|password|passwd|token|private|key|keys|id_rsa|id_ed25519)([._-]|$)", re.I)


class PayloadError(ValueError):
    pass


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_path(path: Path, *, regular: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise PayloadError("absolute path without '..' required")
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise PayloadError("symlinks are not permitted")
    if regular and (not path.is_file() or not stat.S_ISREG(path.stat().st_mode)):
        raise PayloadError("regular file required")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_dir(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def durable(path: Path, value: dict) -> None:
    safe_path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


def read_object(path: Path) -> dict:
    safe_path(path, regular=True)
    with path.open(encoding="utf8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise PayloadError("summary must be a JSON object")
    return result


def complete_summary(summary: dict) -> bool:
    totals = summary.get("totals")
    if (summary.get("status") != COMPLETE
            or type(summary.get("completed_submissions")) is not int
            or summary["completed_submissions"] < 0 or not isinstance(totals, dict)):
        return False
    if any(type(totals.get(key)) is not int or totals[key] < 0
           for key in ("attempts", "settled", "unknown", "unsettled")):
        return False
    return totals["attempts"] == totals["settled"] and totals["unknown"] == totals["unsettled"] == 0


def checkpoint_from_line(line: bytes) -> dict | None:
    try:
        record = json.loads(line.decode("utf8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("case_id"), str):
        return None
    completed = record.get("completed_submissions", record.get("completed"))
    if type(completed) is not int or completed < 0:
        return None
    return {"completed_submissions": completed, "case_id": record["case_id"],
            "status": record.get("status"), "updated_at": utc()}


def _secret_path(source: Path) -> bool:
    return (any(_SECRET.search(part) or part.lower() in {".ssh", ".aws", ".azure"} for part in source.parts)
            or source.suffix.lower() in {".pem", ".p12", ".pfx", ".kdbx"})


def _snapshot(source: Path, output: Path, index: int) -> dict:
    safe_path(source, regular=True)
    if _secret_path(source):
        raise PayloadError("credential/environment/secret snapshots are forbidden")
    destination = safe_path(output / "receipts")
    destination.mkdir(mode=0o700, exist_ok=True)
    target = safe_path(destination / f"{index:04d}-{source.name}")
    before = sha256_file(source)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    copied = sha256_file(target)
    after = sha256_file(source)
    sync_dir(destination)
    return {"source": str(source), "snapshot": target.relative_to(output).as_posix(),
            "sha256": copied, "bytes": target.stat().st_size,
            "stable_during_copy": before == after == copied}


def run_payload(driver: Path, driver_sha256: str, summary: Path,
                receipts: list[Path] | tuple[Path, ...] = (), *, output: Path | None = None) -> int:
    output = safe_path(Path(os.environ["ECS_ACCEPTANCE_OUTPUT_DIR"]) if output is None else output)
    if not output.is_dir():
        raise PayloadError("existing job output directory required")
    driver, summary = safe_path(Path(driver), regular=True), safe_path(Path(summary))
    if not re.fullmatch(r"[0-9a-f]{64}", driver_sha256) or sha256_file(driver) != driver_sha256:
        raise PayloadError("driver_sha256 mismatch")
    # Persist before Popen. A crash, failure or missing result never releases it.
    claim = output / "http-driver-started.json"
    if claim.exists() or claim.is_symlink():
        raise FileExistsError("driver already claimed; no second execution")
    if summary.exists():
        raise PayloadError("fresh task summary must not preexist; historical evidence is not reused")
    with claim.open("x", encoding="utf8") as stream:
        json.dump({"driver": str(driver), "driver_sha256": driver_sha256,
                   "started_at": utc(), "no_retry": True}, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(output)
    result = {"runtime_complete": False, "status": "INCOMPLETE_UNCONFIRMED",
              "quality_review_required": True, "acceptance_verdict": "NOT_EVALUATED",
              "driver_sha256": driver_sha256, "no_retry": True}
    returncode = None
    observation_errors = []
    try:
        process = subprocess.Popen([sys.executable, str(driver)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=None, close_fds=True)
        assert process.stdout is not None
        try:
            with process.stdout:
                for line in iter(process.stdout.readline, b""):
                    try:
                        binary_stdout = getattr(sys.stdout, "buffer", None)
                        if binary_stdout is not None:
                            binary_stdout.write(line)
                            binary_stdout.flush()
                        else:
                            sys.stdout.write(line.decode("utf8", errors="replace"))
                            sys.stdout.flush()
                    except Exception as exc:
                        observation_errors.append({"stage": "stdout_tee", "error_type": type(exc).__name__, "error": str(exc)})
                    try:
                        checkpoint = checkpoint_from_line(line)
                        if checkpoint is not None:
                            durable(output / "checkpoint.json", checkpoint)
                    except Exception as exc:
                        observation_errors.append({"stage": "checkpoint", "error_type": type(exc).__name__, "error": str(exc)})
        finally:
            # Observation failures cannot finalize the task while its original
            # HTTP driver is still running. Keep draining; never retry or kill.
            returncode = process.wait()
        result["driver_exit_code"] = returncode
        if sha256_file(safe_path(driver, regular=True)) != driver_sha256:
            raise PayloadError("frozen driver changed during execution")
        saved = read_object(summary)
        result["summary_status"] = saved.get("status")
        result["completed_submissions"] = saved.get("completed_submissions")
        totals = saved.get("totals")
        if isinstance(totals, dict):
            result["totals"] = totals
            for key in ("attempts", "settled", "unknown", "unsettled", "actual_tokens", "held_tokens"):
                if key in totals:
                    result[key] = totals[key]
        if returncode == 0 and complete_summary(saved) and not observation_errors:
            result.update(runtime_complete=True, status=COMPLETE)
        else:
            result["reason"] = "driver_failed_or_summary_incomplete_no_retry"
    except Exception as exc:
        result.update(error_type=type(exc).__name__, reason=str(exc), driver_exit_code=returncode)
    finally:
        if observation_errors:
            result["observation_errors"] = observation_errors
        snapshots = []
        errors = []
        selected = list(dict.fromkeys([summary, *map(Path, receipts)]))
        for index, source in enumerate(selected):
            try:
                item = _snapshot(source, output, index)
                snapshots.append(item)
                if not item["stable_during_copy"]:
                    errors.append({"source": str(source), "error": "snapshot changed during copy"})
            except (OSError, ValueError) as exc:
                errors.append({"source": "REDACTED_SECRET_PATH" if _secret_path(source) else str(source),
                               "error_type": type(exc).__name__, "error": str(exc)})
        try:
            durable(output / "receipt-snapshots.json", {"files": snapshots, "errors": errors, "created_at": utc()})
        except (OSError, ValueError) as exc:
            errors.append({"error_type": type(exc).__name__, "error": str(exc), "stage": "snapshot_manifest"})
        if errors:
            result.update(runtime_complete=False, status="INCOMPLETE_UNCONFIRMED",
                          snapshot_errors=errors)
        result["finished_at"] = utc()
        try:
            durable(output / "result.json", result)
        except (OSError, ValueError) as exc:
            result["runtime_complete"] = False
            print(json.dumps({"status": "INCOMPLETE_UNCONFIRMED", "result": result,
                              "persistence_error": str(exc)}, ensure_ascii=True), file=sys.stderr, flush=True)
    return 0 if result["runtime_complete"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--driver-sha256", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        return run_payload(args.driver, args.driver_sha256, args.summary, args.receipt)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)}), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
