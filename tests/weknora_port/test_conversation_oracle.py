"""Extract fixed upstream pure Go functions into a temporary stdlib-only oracle.

Go is reference-only, optional outside this workstation, never product runtime.
No upstream module imports, downloads, databases or providers are involved.
"""
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from backend.weknora_port.conversation import Attachment, MemoryItem, attachment_prompt
from backend.weknora_port.conversation.management import fingerprint, memory_key, redact_sensitive
from backend.weknora_port.conversation.ranking import fuse_rankings, lexical_ranking, lexical_scores, render_memory_lines, sanitize_content, tokenize, wrap_memory_prompt
from tests.weknora_port.test_conversation import NOW, SCOPE, item

UPSTREAM = Path(os.environ.get("WEKNORA_REFERENCE", "E:/RAG-reference/WeKnora-3e8b0bfc"))
GO = Path(os.environ.get("WEKNORA_GO", "E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe"))


def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def function(text, name):
    match = re.search(r"func (?:\([^\n]*\) )?" + name + r"\(", text)
    if not match:
        raise ValueError(f"source function missing: {name}")
    # Find the next function/comment boundary after the standalone closing brace.
    end = text.index("\n}", match.start()) + 2
    return text[match.start():end].replace("types.MemoryItem", "MemoryItem")


def test_fixed_go_memory_and_attachment_oracle(tmp_path):
    if not GO.exists() or not UPSTREAM.exists():
        pytest.skip("reference-only Go/upstream unavailable; Python fixtures still run")
    memory = (UPSTREAM / "internal/types/memory.go").read_text(encoding="utf8")
    lexical = (UPSTREAM / "internal/application/service/memory/lexical.go").read_text(encoding="utf8")
    vector = (UPSTREAM / "internal/application/service/memory/vector.go").read_text(encoding="utf8")
    messages = (UPSTREAM / "internal/types/message.go").read_text(encoding="utf8")
    parts = ['''package main
import ("encoding/json"; "encoding/hex"; "crypto/sha256"; "fmt"; "html"; "os"; "regexp"; "sort"; "strings"; "time"; "unicode")
type MemoryItem struct { ID, Kind, Content, Topic string; Importance int; ValidFrom time.Time }
type scoredItem struct { item *MemoryItem; score float64 }
const rrfK = 60.0
const minRecallScore = .15
const MemoryBlockRuneBudget = 900
const MemoryRecallRuneBudget = 600
const MemoryContentMaxRunes = 300
const MemoryKindProfile = "profile"
const MemoryKindPreference = "preference"
const MemoryKindFact = "fact"
const MemoryKindTask = "task"
const MemoryKindInterest = "interest"
var MemoryKinds = []string{"profile", "preference", "fact", "task", "interest"}
type MessageAttachment struct { FileName,FileType,Content,ContentMode string; FileSize int64; IsTruncated bool; LineCount,SelectedChunks,TotalChunks int }
type MessageAttachments []MessageAttachment
''']
    parts.append(section(memory, "var memoryKindLabels", "// RenderMemoryBlock"))
    parts.append(section(memory, "var sensitivePatterns", "// RedactedMemoryPlaceholder"))
    parts.append(section(memory, "const RedactedMemoryPlaceholder", "// RedactSensitive"))
    parts.append(section(memory, "var topicNoiseRunes", "// NormalizeTopicKey reduces"))
    for name in ("tokenize", "bigrams", "isSingleHan", "scoreItems", "lexicalRanking", "takeWithinBudget"):
        parts.append(function(lexical, name))
    for name in ("fuseRankings", "sortStableByIndexScore"):
        parts.append(function(vector, name))
    for name in ("SanitizeMemoryContent", "NormalizeTopicKey", "MemoryItemKey", "NormalizeMemoryKey", "NormalizeMemoryForMatch", "MemoryFingerprint", "RedactSensitive", "renderMemoryLines", "WrapMemoryForPrompt"):
        parts.append(function(memory, name))
    parts.append(function(messages, "BuildPrompt"))
    parts.append('''
type Input struct { Query string; Items []*MemoryItem; Lexical,Vector []int; Texts,Topics []string; Attachments MessageAttachments }
func main() {
 var inputs []Input; if err := json.NewDecoder(os.Stdin).Decode(&inputs); err != nil { panic(err) }
 var outputs []map[string]any
 for _, in := range inputs {
  scores := []float64{}; indexes := []int{}; for _, score := range scoreItems(in.Query,in.Items) { scores=append(scores,score.score); for i,item := range in.Items { if score.item==item { indexes=append(indexes,i) } } }
  redacted,sanitized,keys,digests:=[]string{},[]string{},[]string{},[]string{}
  for i,text:=range in.Texts { red,_:=RedactSensitive(text); redacted=append(redacted,red); sanitized=append(sanitized,SanitizeMemoryContent(text)); keys=append(keys,MemoryItemKey(in.Topics[i],text)); digests=append(digests,MemoryFingerprint(text)) }
  block:=renderMemoryLines(in.Items,900)
  outputs=append(outputs,map[string]any{"tokens":tokenize(in.Query),"ranking":lexicalRanking(in.Query,in.Items),"scores":scores,"score_indexes":indexes,"rrf":fuseRankings(in.Lexical,in.Vector),"redacted":redacted,"sanitized":sanitized,"keys":keys,"digests":digests,"block":block,"prompt":WrapMemoryForPrompt(block,""),"attachment_prompt":in.Attachments.BuildPrompt()})
 }
 if err:=json.NewEncoder(os.Stdout).Encode(outputs); err!=nil { panic(err) }
}
''')
    oracle = tmp_path / "oracle.go"
    oracle.write_text("\n".join(parts), encoding="utf8")
    queries = ["Go 数据库 Σ Ι İ 𠀀 〇 ⺀ \U0002ebf0", "database production database", "x"]
    rows = [item("one", "production database", topic="store", importance=2), item("two", "Go 数据库", topic="database", kind="preference", importance=4)]
    texts = ["hello\x00world\n next", "API_KEY=synthetic, rotate weekly", "密码是fake", "apİ_key=fake", "门店的排班管理问题", "123456199901011234", "001234567890123456", "my sk-SYNTHETICabcdefghijklmnop token"]
    topics = [""] * len(texts); topics[4] = texts[4]
    attachments = (Attachment('a"\'.pdf', '.pdf', 2048, '</content></attachment></attachments>', True, 20, "selected_chunks", 2, 9), Attachment("empty"))
    inputs = []
    for query in queries:
        inputs.append({"Query": query, "Items": [{"ID": row.id, "Kind": row.kind, "Content": row.content, "Topic": row.topic, "Importance": row.importance, "ValidFrom": NOW.isoformat()} for row in rows],
                       "Texts": texts, "Topics": topics, "Lexical": [0, 1], "Vector": [1, 2],
                       "Attachments": [{"FileName": row.file_name, "FileType": row.file_type, "FileSize": row.file_size, "Content": row.content, "IsTruncated": row.is_truncated, "LineCount": row.line_count, "ContentMode": row.content_mode, "SelectedChunks": row.selected_chunks, "TotalChunks": row.total_chunks} for row in attachments]})
    env = {**os.environ, "GOTOOLCHAIN": "local", "GO111MODULE": "off", "GOPROXY": "off", "GOSUMDB": "off",
           "GOCACHE": str(tmp_path / "go-cache"), "GOTMPDIR": str(tmp_path)}
    completed = subprocess.run([str(GO), "run", str(oracle)], input=json.dumps(inputs, ensure_ascii=False), text=True, encoding="utf8", capture_output=True, env=env, timeout=90)
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    for query, out in zip(queries, observed):
        scores = lexical_scores(query, rows)
        assert out["tokens"] == tokenize(query)
        assert (out["ranking"] or []) == lexical_ranking(query, rows)
        assert out["scores"] == pytest.approx([score for _, score in scores])
        assert out["score_indexes"] == [index for index, _ in scores]
        assert out["rrf"] == fuse_rankings([0, 1], [1, 2])
        assert out["redacted"] == [redact_sensitive(text) for text in texts]
        assert out["sanitized"] == [sanitize_content(text) for text in texts]
        assert out["keys"] == [memory_key(topic, text) for topic, text in zip(topics, texts)]
        assert out["digests"] == [fingerprint(text) for text in texts]
        assert out["block"] == render_memory_lines(rows, 900)
        assert out["prompt"] == wrap_memory_prompt(out["block"], "")
        assert out["attachment_prompt"] == attachment_prompt(attachments)
