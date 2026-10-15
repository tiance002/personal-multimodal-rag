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


def test_summary_recovery_matches_executed_go_source():
    oracle=json.loads((Path(__file__).parent/'fixtures/summary_json_go_oracle.json').read_text(encoding='utf-8'))
    assert oracle['exit_code']==0 and len(oracle['rows'])==len(oracle['cases']) and oracle['provider_calls']==0
    for row in oracle['rows']:
        assert parse_summary(oracle['cases'][row['index']])==(row['summary'],row['profile'])


def test_source_summary_configuration_id_resolved_text_language_and_positive_limits():
    from backend.weknora_port.document_profile import summary_configuration
    from backend.weknora_port.runtime import ConversationSettings
    from dataclasses import replace
    rows=[{'id':'default_summary','content':'DEFAULT {{language}}'}, {'id':'custom','content':'CUSTOM {{language}}'}]
    cfg=ConversationSettings()
    assert summary_configuration(cfg,rows)==('DEFAULT Chinese (Simplified)',8192,1024)
    cfg=replace(cfg,generate_summary_prompt_id='custom',summary={'max_input_chars':0,'max_completion_tokens':-2})
    assert summary_configuration(cfg,rows,'en-US')==('CUSTOM English',8192,1024)
    assert summary_configuration(replace(cfg,generate_summary_prompt='RESOLVED {{language}}'),rows,'ja')==('RESOLVED Japanese',8192,1024)
    assert summary_configuration(replace(cfg,generate_summary_prompt_id='missing'),rows)[0]==''
    assert summary_configuration(cfg,rows,'Unlisted Display Name')[0]=='CUSTOM Unlisted Display Name'
    # Source backfillConversationDefaults: a missing ID retains raw text.
    mapping={'conversation':{'generate_summary_prompt_id':'custom','generate_summary_prompt':'RAW YAML'}}
    assert ConversationSettings.from_mapping(mapping,prompt_templates={}).generate_summary_prompt=='RAW YAML'
    assert ConversationSettings.from_mapping(mapping,prompt_templates={'generate_summary':rows}).generate_summary_prompt=='CUSTOM {{language}}'


def test_qa_summary_and_fallback_resolution_match_executed_go_backfill():
    from backend.weknora_port.conversation_configuration import resolve_startup_templates
    oracle=json.loads((Path(__file__).parent/'fixtures/configuration_go_qa_20261015.json').read_text(encoding='utf-8'))
    assert oracle['exit_code']==0
    templates={'generate_summary':[{'id':'known','content':'RESOLVED SUMMARY'}],
               'fallback':[{'id':'known-fallback','content':'RESOLVED FALLBACK'}]}
    cases=[r for r in oracle['rows'] if r['kind'] in ('qa_summary','qa_fallback')]
    assert len(cases)==6
    for row in cases:
        prefix='generate_summary' if row['kind']=='qa_summary' else 'fallback'
        raw='RAW YAML' if prefix=='generate_summary' else 'RAW FALLBACK'
        result=resolve_startup_templates({prefix+'_prompt_id':row['input'],prefix+'_prompt':raw},templates)
        assert result[prefix+'_prompt']==row['output']
