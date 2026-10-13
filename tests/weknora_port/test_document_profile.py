"""Source profile/sample oracle + successful model-output interpretation."""
import json
from pathlib import Path
from backend.weknora_port.document_profile import normalize_profile,sample_long_content,parse_summary,real_text


def test_fixed_source_profile_and_sample_same_fixture():
    oracle=json.loads((Path(__file__).parent/'fixtures/document_profile_go_oracle.json').read_text(encoding='utf-8'))
    assert oracle['exit_code']==0 and oracle['provider_calls']==0
    assert len(oracle['rows'])==8
    for row in oracle['rows']:
        if row['kind']=='profile':
            assert normalize_profile(oracle['profile_inputs'][row['index']])==row['output']
        else:
            assert sample_long_content(oracle['sample_input'],row['limit'])==row['output']

def test_source_summary_plain_json_gist_and_empty_shapes():
    assert parse_summary('  Legacy plain summary  ')==('Legacy plain summary',None)
    assert parse_summary('{"summary":"x","gist":" y ","topics":["k","K."]}')==('x',{'gist':'y','topics':['k']})
    assert parse_summary('```json\n{"gist":"g"}\n```')==('g',{'gist':'g'})
    assert parse_summary('{"topics":[]}')==('{"topics":[]}',None)
    assert parse_summary('{"summary":123}')==('{"summary":123}',None)
    assert real_text('![x](public.png)<image><image_ocr>real OCR words</image_ocr></image>')=='real OCR words'
