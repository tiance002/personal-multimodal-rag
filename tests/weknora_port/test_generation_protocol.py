"""Exact protocol/source-message fixtures; runtime never reads reference tree."""
import hashlib
from backend.weknora_port.references.registry import SourceRegistry

''
EXPECTED_ENABLED_SHA256 = '880e15ee675bcadb2a6fa98aa55521aff244482bebe3c06761f49c0286eb2812'
EXPECTED_DISABLED_SHA256 = '73c6be34e0b6727fab98e8fa098438261e9862663eaea1dbaa5721f92cb7e161'

def test_protocol_is_exact_fixed_source_with_mcp_routing_no_execution():
    for flag,expected in ((True,EXPECTED_ENABLED_SHA256),(False,EXPECTED_DISABLED_SHA256)):
        value=SourceRegistry(flag).protocol_prompt
        assert hashlib.sha256(value.encode()).hexdigest()==expected
        assert value.startswith('\n\n## Source handling protocol')
        assert value.endswith('Copy them exactly from the directory or describe result; never invent them.\n')
        assert 'MCP routing uses request-local msN' in value
        assert 'sandbox:<file name>' in value
