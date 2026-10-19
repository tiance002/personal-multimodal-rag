"""Fixed-source citation registry/decoder behavior; no provider or DB calls."""
import pytest

from backend.weknora_port.references import ChunkReference, CitationStreamDecoder, SourceRegistry


def test_current_evidence_expands_but_historical_navigation_handle_does_not():
    registry = SourceRegistry()
    historic = registry.register_chunk(ChunkReference("old-chunk", document_title="Old"), evidence=False)
    current = registry.register_chunk(ChunkReference("chunk-1", knowledge_base_id="kb-2",
                                                     document_title='A "quoted" title'))
    assert (historic, current) == ("c1", "c2")
    assert registry.expand_text(f"old <ref id=\"{historic}\"/> new <ref id=\"{current}\"/>") == (
        'old  new <kb doc="A &quot;quoted&quot; title" chunk_id="chunk-1" kb_id="kb-2" />')
    assert registry.invalid_handles == [historic]


def test_known_handle_cannot_be_rebound_and_web_fragment_deduplicates():
    registry = SourceRegistry()
    assert registry.register_chunk(ChunkReference("source-A")) == "c1"
    assert registry.register_chunk(ChunkReference("c1", document_title="forged")) == "c1"
    assert registry.expand_text('<ref id="c1"/>') == '<kb doc="" chunk_id="source-A" />'
    first = registry.register_web("https://example.test/path#one", "Page")
    second = registry.register_web("https://example.test/path#two", "")
    assert first == second == "w1"
    assert registry.expand_text(f'<ref id="{first}"/>') == (
        '<web url="https://example.test/path#one" title="Page" />')


def test_replayed_public_citations_compact_to_navigation_only_handles():
    registry = SourceRegistry()
    compact = registry.compact_public_citations(
        '<kb doc="Old &amp; Known" chunk_id="old-1" kb_id="kb-old" /> '
        '<web url="https://example.test/article#part" title="A &quot;page&quot;" />')
    assert compact == '<ref id="c1"/> <ref id="w1"/>'
    assert registry.expand_text(compact) == " "
    assert registry.invalid_handles == ["c1", "w1"]
    current = registry.compact_public_citations(
        '<kb doc="New" chunk_id="new-1" kb_id="kb-new" />', evidence=True)
    assert registry.expand_text(current) == '<kb doc="New" chunk_id="new-1" kb_id="kb-new" />'


def test_public_tags_unknown_handles_disabled_refs_and_invalid_observation():
    registry = SourceRegistry()
    assert registry.expand_text('x <kb doc="forged"/> <web url="evil"/> <ref id="c99"/> y') == "x    y"
    assert registry.invalid_handles == ["c99"]
    disabled = SourceRegistry(citations_enabled=False)
    assert disabled.expand_text('x <ref id="c1"/>') == "x "


def test_split_markers_are_held_expanded_or_dropped_at_flush():
    registry = SourceRegistry()
    handle = registry.register_chunk(ChunkReference("real"))
    decoder = CitationStreamDecoder(registry)
    assert decoder.feed("A <re") == "A "
    assert decoder.feed(f'f id="{handle}"') == ""
    assert decoder.feed("/> B") == '<kb doc="" chunk_id="real" /> B'
    assert decoder.feed(" <ref id=\"c404\"/") == " "
    assert decoder.flush() == ""
    tail = CitationStreamDecoder(registry)
    assert tail.feed("safe <ref id=\"c1\"") == "safe "
    assert tail.flush() == ""
    forged = CitationStreamDecoder(registry)
    assert forged.feed(' <kb doc="x"') == " "
    assert forged.flush() == ""


def test_attempt_clones_keep_deterministic_maps_but_isolate_candidate_observations():
    base = SourceRegistry()
    historical = base.register_chunk(ChunkReference("prior", document_title="Prior"), evidence=False)
    current = base.register_chunk(ChunkReference("retrieved", knowledge_base_id="kb", document_title="Current"))
    web = base.register_web("https://example.test/result#fragment", "Result")
    base.register_document("doc-1")
    base.register_knowledge_base("kb-1")
    snapshot = base.snapshot()

    cheap = base.clone_for_attempt()
    expensive = base.clone_for_attempt()
    assert cheap.snapshot() == expensive.snapshot() == snapshot
    cheap.register_chunk(ChunkReference("cheap-only"))
    assert cheap.expand_text(f'<ref id="{historical}"/><ref id="{current}"/><ref id="c999"/>') == (
        '<kb doc="Current" chunk_id="retrieved" kb_id="kb" />')
    # A fresh attempt sees the same initial mappings and no observations from Cheap.
    assert expensive.invalid_count == 0 and expensive.used_handles == set()
    assert expensive.expand_text(f'<ref id="{current}"/><ref id="{web}"/>') == (
        '<kb doc="Current" chunk_id="retrieved" kb_id="kb" />'
        '<web url="https://example.test/result#fragment" title="Result" />')
    assert expensive.used_handles == {current, web}
    assert cheap.invalid_handles == [historical, "c999"]
    cheap.reset_attempt_observations()
    assert cheap.snapshot().chunks[:2] == snapshot.chunks[:2]
    assert cheap.invalid_count == 0 and not cheap.used_handles


def test_resource_mask_roundtrips_markdown_images_and_clones_per_attempt():
    base = SourceRegistry()
    original = "![diagram](storage://acct/local://10000/files/image.png) and summary/12345678-1234-1234-1234-123456789abc"
    encoded = base.encode_resource_text(original)
    assert encoded == "![diagram](res://0001) and res://0002"
    cheap = base.clone_for_attempt()
    expensive = base.clone_for_attempt()
    # Identical base snapshots assign identical handles independently.
    assert cheap.encode_resource_text(original) == expensive.encode_resource_text(original) == encoded
    # Binary/image payloads are not implicitly traversed by text masking.
    binary_payload = b"\x00\x01res://0001"
    assert binary_payload == b"\x00\x01res://0001"
    assert expensive.decode_output_text(encoded) == original

    decoder = CitationStreamDecoder(expensive)
    assert decoder.feed("image ![x](res://00") == "image ![x]("
    assert decoder.feed("01)") == "storage://acct/local://10000/files/image.png)"
    assert decoder.flush() == ""


def test_protocol_prompt_preserves_image_url_exception_and_disabled_citation_policy():
    enabled = SourceRegistry().protocol_prompt
    disabled = SourceRegistry(citations_enabled=False).protocol_prompt
    assert "Do not embed retrieved images" in disabled
    assert '<ref id="cN"/>' not in disabled
    assert "res://NNNN" in enabled and "res://NNNN" in disabled


def test_disabled_decoder_strips_all_transport_tags_but_preserves_markdown_literals():
    registry = SourceRegistry(citations_enabled=False)
    decoder = CitationStreamDecoder(registry)
    text = (
        'A <ref>bare</ref> <ref/> <ref id="c1"/> '
        '<kb doc="x"/> </kb> <web url="https://bad.test"/> </web> '
        '`literal <ref> </ref> <kb/>` and ```html\n<web/>\n``` '
        '[link](https://example.test/a) ![image](https://example.test/i.png)'
    )
    expected = (
        'A bare       `literal <ref> </ref> <kb/>` and ```html\n<web/>\n``` '
        '[link](https://example.test/a) ![image](https://example.test/i.png)'
    )
    assert registry.decode_output_text(text) == expected
    assert decoder.feed(text) + decoder.flush() == expected


def test_disabled_citations_strip_only_private_source_attribution_line():
    registry = SourceRegistry(citations_enabled=False)
    text = (
        "Answer keeps ordinary c1 and d1 strings.\n"
        "Source: chapter c1 and dataset d1.\n"
        "Source: https://example.test/c1/d1\n"
        "\n**Source:** *Report* (document d1, chunk c1)\n"
        "\n```text\nSource: code (document d1, chunk c1)\n```\n"
        "`Source: inline (document d1, chunk c1)`\n"
        "![image](https://example.test/image.png) [link](https://example.test/page)"
    )
    expected = (
        "Answer keeps ordinary c1 and d1 strings.\n"
        "Source: chapter c1 and dataset d1.\n"
        "Source: https://example.test/c1/d1\n"
        "\n\n```text\nSource: code (document d1, chunk c1)\n```\n"
        "`Source: inline (document d1, chunk c1)`\n"
        "![image](https://example.test/image.png) [link](https://example.test/page)"
    )
    assert registry.decode_output_text(text) == expected

    enabled = SourceRegistry(citations_enabled=True)
    assert enabled.decode_output_text("Source: Report (document d1, chunk c1)") == (
        "Source: Report (document d1, chunk c1)"
    )


def test_disabled_sse_never_publishes_split_private_source_attribution():
    source = (
        "Answer keeps c1 and d1.\n\n"
        "**Source:** *11-21-16-Updated-Post-Election-Release* "
        "(document d1, chunk c1)\n"
        "![image](https://example.test/image.png) [link](https://example.test/page)"
    )
    expected = SourceRegistry(citations_enabled=False).decode_output_text(source)
    decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
    published = []
    assert decoder.feed("Ordinary answer streams immediately") == "Ordinary answer streams immediately"
    for char in source:
        delta = decoder.feed(char)
        published.append(delta)
        assert "document d1, chunk c1" not in "".join(published)
    published.append(decoder.flush())
    assert "".join(published) == expected
    assert "Answer keeps c1 and d1." in "".join(published)
    assert "![image](https://example.test/image.png)" in "".join(published)
    assert "[link](https://example.test/page)" in "".join(published)

    # EOF without a newline still receives the same filtering as persisted text.
    no_newline = "Answer\n\nSource: Report (document d1, chunk c1)."
    decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
    actual = "".join(decoder.feed(char) for char in no_newline) + decoder.flush()
    assert actual == SourceRegistry(citations_enabled=False).decode_output_text(no_newline)
    assert "document d1, chunk c1" not in actual


def test_disabled_decoder_withholds_observed_chinese_attribution_without_buffering_normal_answer():
    attribution = (
        "**依据说明：**\n"
        "上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`），"
        "该段明确列出了对应线性方程组的系数矩阵和增广矩阵。"
    )
    source = (
        "普通答案先即时输出，提到 c1 和 d1 也保留。\n\n"
        f"{attribution}\n\n"
        "另有普通正文：文档 d1 的第2段包含 chunk c2。\n"
        "**依据说明：**\n这里是用户可见解释，包含 c1/d1，但不符合来源句式。\n"
        "```text\n**依据说明：** 上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`）\n```\n"
        "![原图](https://example.test/image.png) [页面](https://example.test/page)"
    )
    expected = (
        "普通答案先即时输出，提到 c1 和 d1 也保留。\n\n\n"
        "另有普通正文：文档 d1 的第2段包含 chunk c2。\n"
        "**依据说明：**\n这里是用户可见解释，包含 c1/d1，但不符合来源句式。\n"
        "```text\n**依据说明：** 上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`）\n```\n"
        "![原图](https://example.test/image.png) [页面](https://example.test/page)"
    )
    registry = SourceRegistry(citations_enabled=False)
    assert registry.decode_output_text(source) == expected

    decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
    assert decoder.feed("普通答案先即时输出，提到 c1 和 d1 也保留。") == "普通答案先即时输出，提到 c1 和 d1 也保留。"
    published = []
    for index, char in enumerate(source):
        published.append(decoder.feed(char))
        current = "".join(published)
        if index < source.index("```text"):
            assert "`d1`" not in current
            assert "chunk `c2`" not in current
    published.append(decoder.flush())
    assert "".join(published) == expected

    # Citations-enabled answers keep the attribution text unchanged.
    assert SourceRegistry(citations_enabled=True).decode_output_text(attribution) == attribution


def test_internal_tag_decoding_is_consistent_at_every_chunk_split():
    registry = SourceRegistry()
    chunk = registry.register_chunk(ChunkReference("real-id", document_title="Real"))
    source = (
        f'Claim <ref id="{chunk}"/>! <ref id="c404"/> '
        '<ref>bare</ref> <ref/> <kb/> </kb><web/> </web> '
        '`<ref id="c404"/> </ref>` and ```text\n<kb/> </web>\n``` '
        '[ordinary](https://example.test/path) ![img](https://example.test/i.png)'
    )
    expected = (
        'Claim <kb doc="Real" chunk_id="real-id" />!  bare     '
        '`<ref id="c404"/> </ref>` and ```text\n<kb/> </web>\n``` '
        '[ordinary](https://example.test/path) ![img](https://example.test/i.png)'
    )
    assert registry.decode_output_text(source) == expected
    for split in range(len(source) + 1):
        decoder = CitationStreamDecoder(registry.clone_for_attempt())
        actual = decoder.feed(source[:split]) + decoder.feed(source[split:]) + decoder.flush()
        assert actual == expected, f"decoder changed at split {split}"


def test_incomplete_closing_tag_is_removed_at_eof_and_across_many_chunks():
    source = "answer </ref>tail </we"
    expected = "answer tail "
    registry = SourceRegistry(citations_enabled=False)
    assert registry.decode_output_text(source) == expected
    decoder = CitationStreamDecoder(registry)
    actual = "".join(decoder.feed(char) for char in source) + decoder.flush()
    assert actual == expected


def test_unmatched_inline_backtick_does_not_hide_later_citations_or_html_text():
    registry = SourceRegistry()
    handle = registry.register_chunk(ChunkReference("valid"))
    source = f"literal ` marker <ref id=\"{handle}\"/> and <reference"
    expected = "literal ` marker <kb doc=\"\" chunk_id=\"valid\" /> and <reference"
    assert registry.decode_output_text(source) == expected
    for split in range(len(source) + 1):
        decoder = CitationStreamDecoder(registry.clone_for_attempt())
        actual = decoder.feed(source[:split]) + decoder.feed(source[split:]) + decoder.flush()
        assert actual == expected, f"decoder changed at split {split}"


def test_s2_real_attribution_spacing_and_each_split_is_hidden():
    text = "答案。\n\n**依据说明：**\n上述数据直接来源于文档 `d1` 中的第 2 段（chunk `c2`），该段明确列出了对应线性方程组的系数矩阵和增广矩阵。"
    expected = "答案。\n\n"
    for split in range(len(text) + 1):
        decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
        first = decoder.feed(text[:split])
        assert "`d1`" not in first and "`c2`" not in first
        assert first + decoder.feed(text[split:]) + decoder.flush() == expected


@pytest.mark.parametrize(("text", "expected"), [
    (
        "根据提供的课件内容（文档 `d1`），针对给定的线性方程组：",
        "根据提供的课件内容，针对给定的线性方程组：",
    ),
    (
        "提供的文档片段（`d1`）仅是对线性方程组矩阵表示方法的**总结性介绍**。",
        "提供的文档片段仅是对线性方程组矩阵表示方法的**总结性介绍**。",
    ),
    (
        "**The Wind in the Willows** was first published in **1908** (source: d2, c2).",
        "**The Wind in the Willows** was first published in **1908** .",
    ),
    ("答案 (source: d1, c2", "答案 "),
    ("根据提供的课件内容（文档 `d1`", "根据提供的课件内容"),
])
def test_s2_real_inline_source_annotations_are_hidden_at_every_split(text, expected):
    # Exact observed forms: S2_MODEL_ROUTING_ANSWER_STABILITY.results.json,
    # s1-omni-matrix-positive/A, m32-omni-summary-only-no-evidence/A and
    # hotpot-5a745a-success/A. Only the explicit source annotation is removed.
    registry = SourceRegistry(citations_enabled=False)
    assert registry.decode_output_text(text) == expected
    for split in range(len(text) + 1):
        decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
        first = decoder.feed(text[:split])
        assert "`d1`" not in first and "d2, c2" not in first
        assert first + decoder.feed(text[split:]) + decoder.flush() == expected
    decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
    published = ""
    for char in text:
        published += decoder.feed(char)
        assert "`d1`" not in published and "d2, c2" not in published
    assert published + decoder.flush() == expected

    enabled = SourceRegistry(citations_enabled=True)
    handle = enabled.register_chunk(ChunkReference("valid-source", document_title="Real"))
    assert enabled.decode_output_text(text + f'<ref id="{handle}"/>') == (
        text + '<kb doc="Real" chunk_id="valid-source" />'
    )


def test_inline_source_filter_preserves_body_identifiers_and_code_examples():
    text = (
        "变量 d1 和 c2；文档 d1 的第2段包含 chunk c2。\n"
        "Source: section c2 and dataset d1.\n"
        "普通括号 (d1, c2) 和（文档 `d1`）保留。\n"
        "普通未闭括号 (source: supporting details\n"
        "根据提供的课件内容（文档尚未提供\n"
        "`根据提供的课件内容（文档 d1），正文。`\n"
        "`(source: d2, c2)`\n"
        "```text\n根据提供的课件内容（文档 `d1`），正文。\n"
        "提供的文档片段（`d1`）是说明。\n(source: d2, c2)\n```"
    )
    assert SourceRegistry(citations_enabled=False).decode_output_text(text) == text
    decoder = CitationStreamDecoder(SourceRegistry(citations_enabled=False))
    assert "".join(decoder.feed(char) for char in text) + decoder.flush() == text
