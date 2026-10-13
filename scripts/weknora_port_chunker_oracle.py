"""Fixed-source, local-only Go oracle for the WeKnora chunker.

The runner copies upstream chunker source bytes unchanged into ignored ``var/``;
only debug logging and markdown linked-image normalization are supplied as tiny
stubs for external package dependencies. It never edits or builds in the source
checkout and does not make network/provider/database calls.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

UPSTREAM = Path(r"E:\RAG-reference\WeKnora-3e8b0bfc")
PIN = "3e8b0bfc80b845b2d4b2ed683994748741450a97"
GO = Path(r"E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe")
ORACLE = Path("var/weknora-port/oracle/chunker")
CHUNKER_DIR = "internal/infrastructure/chunker"

_DOC_PARSER_STUB = r'''package docparser
import "regexp"
var reLinkedImage = regexp.MustCompile(`\[!\[([^\]]*)\]\(([^()\s]*(?:\([^)]*\)[^()\s]*)*)\)\]` + `\([^()\s]*(?:\([^)]*\)[^()\s]*)*\)`)
func UnwrapLinkedImages(markdown string) string { return reLinkedImage.ReplaceAllString(markdown, "![$1]($2)") }
'''
_LOGGER_STUB = r'''package logger
import "context"
func Debugf(_ context.Context, _ string, _ ...interface{}) {}
'''
_GO_TEST = r'''package chunker_test
import (
    "encoding/json"
    "fmt"
    "os"
    "testing"
    chunker "github.com/Tencent/WeKnora/internal/infrastructure/chunker"
)
type config struct { ChunkSize int `json:"chunk_size"`; ChunkOverlap int `json:"chunk_overlap"`; Separators []string `json:"separators"`; Strategy string `json:"strategy"`; TokenLimit int `json:"token_limit"`; Languages []string `json:"languages"`; ParentChild bool `json:"parent_child"`; ParentChunkSize int `json:"parent_chunk_size"`; ChildChunkSize int `json:"child_chunk_size"` }
type input struct { Name string `json:"name"`; Text string `json:"text"`; Config config `json:"config"` }
type chunk struct { Content string `json:"content"`; Seq int `json:"seq"`; Start int `json:"start"`; End int `json:"end"`; ContextHeader string `json:"context_header"` }
type child struct { Chunk chunk `json:"chunk"`; ParentIndex int `json:"parent_index"` }
type result struct { Name string `json:"name"`; Chunks []chunk `json:"chunks"`; Parents []chunk `json:"parents"`; Children []child `json:"children"`; SelectedTier string `json:"selected_tier"`; TierChain []string `json:"tier_chain"`; Rejected []any `json:"rejected"` }
func convert(c chunker.Chunk) chunk { return chunk{Content:c.Content, Seq:c.Seq, Start:c.Start, End:c.End, ContextHeader:c.ContextHeader} }
func TestEmitChunkerOracle(t *testing.T) {
    raw, err := os.ReadFile("../../../cases.json"); if err != nil { t.Fatal(err) }
    var cases []input; if err=json.Unmarshal(raw,&cases); err != nil { t.Fatal(err) }
    for _, c := range cases {
        cfg := chunker.SplitterConfig{ChunkSize:c.Config.ChunkSize, ChunkOverlap:c.Config.ChunkOverlap, Separators:c.Config.Separators, Strategy:c.Config.Strategy, TokenLimit:c.Config.TokenLimit, Languages:c.Config.Languages}
        out := result{Name:c.Name}
        if c.Config.ParentChild {
            parentCfg, childCfg := chunker.DeriveParentChildConfigs(cfg, c.Config.ParentChunkSize, c.Config.ChildChunkSize)
            pc := chunker.SplitParentChild(c.Text, parentCfg, childCfg)
            for _, p := range pc.Parents { out.Parents=append(out.Parents,convert(p)) }
            for _, ch := range pc.Children { out.Children=append(out.Children,child{Chunk:convert(ch.Chunk),ParentIndex:ch.ParentIndex}) }
        } else {
            chunks, d := chunker.SplitWithDiagnostics(c.Text,cfg)
            for _, ch := range chunks { out.Chunks=append(out.Chunks,convert(ch)) }
            out.SelectedTier=string(d.SelectedTier)
            for _, tier := range d.TierChain { out.TierChain=append(out.TierChain,string(tier)) }
            for _, rejection := range d.Rejected { out.Rejected=append(out.Rejected,map[string]string{"tier":string(rejection.Tier),"reason":rejection.Reason}) }
        }
        b, err := json.Marshal(out); if err != nil { t.Fatal(err) }; fmt.Printf("ORACLE %s\n", b)
    }
}
'''


def _run(argv: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, check=False, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")


def _checked_git(*args: str) -> str:
    result = _run(("git", "-C", str(UPSTREAM), *args))
    if result.returncode:
        raise RuntimeError(f"UPSTREAM_GIT_FAILED:{result.returncode}:{args[0]}")
    return result.stdout.strip()


def prepare_oracle(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not UPSTREAM.exists() or not GO.exists():
        raise FileNotFoundError("FIXED_UPSTREAM_OR_GO_UNAVAILABLE")
    actual_pin = _checked_git("rev-parse", "HEAD")
    if actual_pin != PIN:
        raise RuntimeError("ORACLE_UPSTREAM_MISMATCH")
    ORACLE.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    source_paths = _checked_git("ls-tree", "-r", "--name-only", "HEAD", "--", CHUNKER_DIR).splitlines()
    for relative in source_paths:
        if not relative.endswith(".go") or relative.endswith("_test.go"):
            continue
        data = subprocess.check_output(("git", "-C", str(UPSTREAM), "show", f"HEAD:{relative}"))
        expected = _checked_git("rev-parse", f"HEAD:{relative}")
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != expected:
            raise RuntimeError(f"ORACLE_SOURCE_HASH_MISMATCH:{relative}")
        target = ORACLE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest[relative] = blob

    for relative, content in {
        "internal/logger/logger.go": _LOGGER_STUB,
        "internal/infrastructure/docparser/image_link_stub.go": _DOC_PARSER_STUB,
    }.items():
        target = ORACLE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    (ORACLE / "go.mod").write_text("module github.com/Tencent/WeKnora\n\ngo 1.26.0\n", encoding="utf-8")
    (ORACLE / "NOTICE").write_text(
        f"Tencent/WeKnora@{PIN}; unmodified internal/infrastructure/chunker/*.go copied by Git blob and verified. "
        "MIT license applies; see fixed upstream LICENSE. Local-only source oracle. Stubs only replace logger.Debugf "
        "(no-op) and docparser.UnwrapLinkedImages (same fixed-source regex/replacement).\n",
        encoding="utf-8",
    )
    (ORACLE / "source_manifest.json").write_text(
        json.dumps({"upstream": PIN, "files": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ORACLE / "cases.json").write_text(json.dumps(list(cases), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    test_path = ORACLE / CHUNKER_DIR / "chunker_oracle_test.go"
    test_path.write_text(_GO_TEST, encoding="utf-8", newline="\n")
    return {"source_manifest": manifest, "test_path": str(test_path)}


def run_chunker_oracle(cases: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = prepare_oracle(cases)
    version = _run((str(GO), "version"))
    if version.returncode:
        raise RuntimeError(f"GO_VERSION_FAILED:{version.returncode}:{version.stdout}")
    result = _run((str(GO), "test", "-v", "-run", "TestEmitChunkerOracle", "./" + CHUNKER_DIR), cwd=ORACLE)
    if result.returncode:
        raise RuntimeError(f"GO_ORACLE_FAILED:{result.returncode}:{result.stdout}")
    rows = [json.loads(line.removeprefix("ORACLE ")) for line in result.stdout.splitlines() if line.startswith("ORACLE ")]
    if len(rows) != len(cases):
        raise RuntimeError(f"GO_ORACLE_OUTPUT_INCOMPLETE:{len(rows)}:{len(cases)}")
    report = {"status": "GO_ORACLE_EXECUTED", "upstream": PIN, "toolchain": version.stdout.strip(),
              "exit_code": result.returncode, "case_count": len(rows), "sources": prepared["source_manifest"]}
    (ORACLE / "execution_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ORACLE / "execution.log").write_text(result.stdout, encoding="utf-8")
    return report, {row["name"]: row for row in rows}
