"""Ordinary RAG source policy; no unrelated tools or guessed sources."""
from backend.weknora_port.references.registry import SourceRegistry


def test_protocol_has_one_active_source_and_resource_policy():
    for flag in (True, False):
        value=SourceRegistry(flag).protocol_prompt
        assert value.startswith('\n\n## Source handling protocol')
        assert value.count('## Source handling protocol') == 1
        assert value.count('## Resource handle protocol') == 1
        assert 'MCP' not in value and 'Wiki' not in value and 'sandbox:' not in value
        assert 'Do not embed retrieved images or reproduce these asset links' in value
        assert 'Use OCR and caption facts as textual evidence' in value


def test_enabled_and_disabled_attribution_are_separate_and_support_images():
    enabled = SourceRegistry(True).protocol_prompt
    disabled = SourceRegistry(False).protocol_prompt
    assert '<ref id="cN"/>' in enabled and '<ref id="cN"/>' not in disabled
    assert 'exact supplied filenames that support the answer' in disabled
    assert 'Do not use a document title to guess a filename or extension' in disabled
    assert 'Do not embed retrieved images' in disabled
    assert 'Source citations are disabled' not in disabled
    assert 'strict JSON/code-only format' in disabled
