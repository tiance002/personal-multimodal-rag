import json
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import pytest
from backend.weknora_port.models.usage_facts import capture_usage
from backend.weknora_port.models.usage import normalize_usage
from backend.weknora_port.source_facts import operation_slot
from backend.weknora_port.authority import load_authority_settings
from backend.weknora_port.models.types import ProviderError


def test_actual_missing_total_and_cache_not_reported_as_zero_or_inferred_actual():
    u=normalize_usage({'prompt_tokens':30,'completion_tokens':10})
    assert u.total_tokens==40  # Existing source internal normalization preserved.
    facts=u.provider_usage_facts
    assert facts.derived_total==40 and facts.get('total').value is None
    assert not facts.get('total').present and not facts.get('cache_read').present
    assert facts.accounting()==(None,40)


@pytest.mark.parametrize('raw,total,lower',[
    ({'prompt_tokens':30,'completion_tokens':10,'total_tokens':40,'prompt_cache_hit_tokens':20,'prompt_cache_miss_tokens':10},40,40),
    ({'total_tokens':0},0,0),
    ({'total_tokens':150},150,150),
    ({'prompt_tokens':150},None,150),
    ({'prompt_cache_hit_tokens':100,'completion_tokens':50},None,150),
    ({'prompt_tokens':50,'completion_tokens':10,'total_tokens':0},None,60),
    ({'prompt_tokens':50,'total_tokens':True},None,50),
    ({'prompt_tokens':50,'completion_tokens':-1},None,50),
    ({'total_tokens':50,'prompt_cache_hit_tokens':100},None,100),
])
def test_reported_total_known_lower_and_cache_never_double_count(raw,total,lower):
    facts=capture_usage(raw)
    assert facts.accounting()==(total,lower)
    assert json.loads(facts.numeric_raw)


def test_model_role_changes_do_not_create_new_recovery_slot():
    assert operation_slot('op/answer/1/cheap','chat')==operation_slot('op/answer/1/expensive','chat')
    assert operation_slot('op/answer/1/cheap','chat')!=operation_slot('op/answer/2/expensive','chat')
    assert operation_slot('op/answer/1/cheap/image-fallback','chat')==operation_slot('op/answer/1/expensive/image-fallback','chat')
    assert operation_slot('op','embedding')==operation_slot('op/embedding-0','embedding')
    assert operation_slot('op/rerank-1','rerank')!=operation_slot('op/rerank-0','rerank')


def test_later_usage_snapshot_cannot_erase_earlier_known_lower_or_add_cache_twice():
    from backend.weknora_port.models.transport import ResponseHandle
    from backend.weknora_port.models.types import TokenUsage
    handle=ResponseHandle(None,TokenUsage())
    handle.capture({'prompt_tokens':150})
    handle.usage=normalize_usage({'prompt_tokens':50,'completion_tokens':0,'total_tokens':50})
    facts=handle.usage.provider_usage_facts
    assert facts.accounting()==(None,150)
    assert facts.get('total').value==50 and facts.prior_numeric_raw


def test_config_default_denied_and_approved_without_reliable_meter_cannot_enable(tmp_path):
    assert load_authority_settings(None) is None
    # Path and hash are not approval; duplicate keys never win by JSON order.
    p=tmp_path/'duplicate.json';p.write_text('{"mode":"synthetic","mode":"approved"}',encoding='utf-8')
    with pytest.raises(ProviderError,match='DUPLICATE'):
        load_authority_settings(p)
