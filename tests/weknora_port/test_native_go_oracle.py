"""Compare Python against actually executed fixed Go request.go functions."""
import json
from pathlib import Path
import pytest
from backend.weknora_port.models import Message,ToolCall,FunctionCall,ChatOptions,ProviderModel
from tests.weknora_port.test_native_protocols import config,TOOLS

FIXTURE=json.loads((Path(__file__).with_name('fixtures')/'native_request_go_oracle.json').read_text(encoding='utf-8'))


def message(raw):
    calls=tuple(ToolCall(tc.get('id',''),tc.get('type','function'),FunctionCall(tc['function'].get('name',''),tc['function'].get('arguments','')),tc.get('provider_metadata') or {}) for tc in raw.get('tool_calls',[]))
    return Message(raw['role'],raw.get('content',''),tuple(raw.get('multi_content') or ()),tuple(raw.get('images') or ()),
                   raw.get('name',''),raw.get('tool_call_id',''),calls,raw.get('reasoning_content',''),raw.get('reasoning_metadata') or {},
                   reasoning_signature=raw.get('reasoning_signature',''))


@pytest.mark.parametrize('case',FIXTURE['cases'],ids=[c['api']+':'+c['level']+':'+str(c['stream']) for c in FIXTURE['cases']])
def test_exact_source_native_renderer_go_oracle(case):
    assert FIXTURE['exit_code']==0 and FIXTURE['provider_calls']==0
    model=ProviderModel(config(case['api']),None)
    options=ChatOptions(max_tokens=256,temperature=.5,top_p=.9,seed=5,reasoning_effort=case['level'],cache_retention='none',tool_choice='required',tools=TOOLS)
    actual=model.build_request_body(tuple(message(m) for m in case['messages']),options,stream=case['stream'])
    assert actual==case['body']
