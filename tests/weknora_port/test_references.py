"""Fixed-source citation registry/decoder behavior; no provider or DB calls."""
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
    assert "relevant image URLs" in disabled
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
