from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from eval_center.retrieval_replay import run_frozen_development_replay
from eval_center.verified_index import IndexReuseRejected


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path(r"D:\RAG-Public-Bench")
DEFAULT_FIXTURE = REPOSITORY_ROOT / "eval_center" / "fixtures" / "rag-retrieval-round1-qids.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen, Development-only retrieval round 1 validation.")
    parser.add_argument("--preflight-only", action="store_true", help="verify clone, model and all index identities without retrieval")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)

    admin_database_url = os.environ.get("RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL")
    if not admin_database_url:
        print(json.dumps({"status": "BLOCKED", "reason": "RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL is not set in this process"}))
        return 2
    if not args.preflight_only and args.output_root is None:
        print(json.dumps({"status": "BLOCKED", "reason": "--output-root is required for retrieval"}))
        return 2

    try:
        result = run_frozen_development_replay(
            repository_root=REPOSITORY_ROOT,
            data_root=args.data_root,
            qid_fixture_path=args.fixture,
            admin_database_url=admin_database_url,
            output_root=args.output_root or Path("unused-preflight-output"),
            preflight_only=args.preflight_only,
        )
    except IndexReuseRejected as exc:
        print(json.dumps({"status": exc.code, "field": exc.field}, sort_keys=True))
        return 2
    except Exception as exc:
        # Never echo exceptions that may contain database URLs or local paths.
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1

    print(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
