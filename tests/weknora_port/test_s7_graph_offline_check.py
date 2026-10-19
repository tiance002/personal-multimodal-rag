"""Offline diagnostic tool contracts; no model/DB fixtures or new sends."""
import asyncio
from dataclasses import asdict
import json

import pytest

from backend.weknora_port.graph.types import GraphSource
from scripts.s7_graph_offline_check import baseline_module, reused_real, synthetic


def test_synthetic_comparison_exposes_baseline_defects_without_hiding_negative_controls():
    baseline, identity = baseline_module("retrieval")
    baseline_extract, _ = baseline_module("extraction")
    result = asyncio.run(synthetic(baseline, baseline_extract))
    assert result["classification"] == "SYNTHETIC_ENGINEERING"
    assert result["new_provider_calls"] == 0
    assert identity["commit"] == "855e2d32bf85f50fbb9fa12ace9b0a51e1adbf67"
    assert any(not item["baseline"]["required_relation_selected"] for item in result["multibranch"])
    assert all(item["current"]["required_relation_selected"] for item in result["multibranch"])
    assert result["cjk_literal"]["baseline"]["edges"] == 0
    assert result["cjk_literal"]["current"]["edges"] == 1
    assert not result["mixed_script_mention"]["baseline"]
    assert result["mixed_script_mention"]["current"]
    assert not any(item["identity_retained"] for item in result["summary_source_identity"]["baseline"]["raw_sources"].values())
    assert all(item["identity_retained"] for item in result["summary_source_identity"]["current"]["raw_sources"].values())
    for arm in result["negative_controls"].values():
        assert arm["unrelated_query_paths"] == arm["reverse_object_paths"] == 0
        assert not arm["identifier_prefix_matched"]
        assert arm["stale_source"]["rejected"]


def test_saved_source_tenant_cannot_be_substituted_for_a_new_scope(tmp_path):
    original = GraphSource(332, "owner", "kb", "doc", "v1", "chunk", "Real source.", 0, 12)
    graph_dir = tmp_path / "acceptance-deepseek"
    graph_dir.mkdir()
    graph_dir.joinpath("graph-0.json").write_text(json.dumps({"chunks": [dict(source=asdict(original),
        source_sha256=original.content_sha256)]}), encoding="utf-8")
    graph_dir.joinpath("graph-1.json").write_text('{"chunks":[]}', encoding="utf-8")
    graph_dir.joinpath("source-quote-replay.json").write_text(json.dumps({"records": [dict(
        document_id="doc", source_sha256=original.content_sha256)]}), encoding="utf-8")
    evidence = tmp_path / "acceptance-api-fixed/evidence"
    evidence.mkdir(parents=True)
    evidence.joinpath("test.stages.json").write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="identity/source SHA mismatch"):
        asyncio.run(reused_real(tmp_path, None))
