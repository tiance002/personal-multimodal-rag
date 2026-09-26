from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine, text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    checks: dict[str, int] = {}
    with engine.connect() as connection:
        checks["active_version_mismatch"] = connection.execute(text("""SELECT count(*) FROM documents d JOIN document_versions v ON v.id=d.active_version_id WHERE v.document_id<>d.id OR v.index_status<>'ready'""")).scalar_one()
        checks["orphan_chunks"] = connection.execute(text("""SELECT count(*) FROM chunks c LEFT JOIN document_versions v ON v.id=c.version_id AND v.document_id=c.document_id WHERE v.id IS NULL""")).scalar_one()
        checks["orphan_asset_versions"] = connection.execute(text("""SELECT count(*) FROM document_assets a LEFT JOIN document_versions v ON v.id=a.version_id AND v.document_id=a.document_id WHERE v.id IS NULL""")).scalar_one()
        checks["orphan_graph_evidence"] = connection.execute(text("""SELECT count(*) FROM graph_edge_evidence e LEFT JOIN chunks c ON c.id=e.chunk_id AND c.version_id=e.version_id WHERE c.id IS NULL""")).scalar_one()
    result = {"status": "PASS" if all(value == 0 for value in checks.values()) else "FAIL", "checks": checks}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

