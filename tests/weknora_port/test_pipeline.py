"""Offline source-derived fixtures: no DB, provider or public network calls."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
import pytest
from backend.weknora_port.models.types import CallContext,TrustedScope,RerankResult,RerankResponse,Message
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState,RetrievalPipeline
from backend.weknora_port.retrieval.query import (parse_understanding,expand_queries,needs_retrieval,understand_query,default_segmenter,TokenizerUnavailable,tokenize)
from backend.weknora_port.retrieval.search import (SearchTarget,SearchParams,search,SearchNothing,SearchError,RetrievalScopeError,deduplicate,partial_overlap)
from backend.weknora_port.retrieval.rerank import rerank,clean_passage,enriched_passage,mmr
from backend.weknora_port.retrieval.merge import merge,sequential,join_content,resolve_parents,expand_neighbors,populate_faq,inject_history
from backend.weknora_port.retrieval.context import into_chat_message,render_placeholders,enriched_chat_passage,validate_input

CTX=CallContext('r',TrustedScope('owner',('kb',)))
TARGET=SearchTarget('kb')
def run(awaitable):return asyncio.run(awaitable)
def row(id='a',content='relevant document evidence',score=.8,**kwargs):
    return SearchResult(id,content,score,knowledge_id='doc',knowledge_base_id='kb',**kwargs)
def state(**kwargs):
    values=dict(query='question evidence',rewrite_query='question evidence',targets=(TARGET,),enable_rewrite=False,enable_expansion=False,context_template='{{contexts}}\n{{query}}');values.update(kwargs);return PipelineState(**values)
class Repo:
    def __init__(self,rows=(),chunks=None,faqs=None):self.rows=list(rows);self.chunks=chunks or {};self.faqs=faqs or {};self.calls=[];self.chunk_calls=[]
    async def hybrid_search(self,targets,params,context,query_embedding=None):
        self.calls.append((targets,params,context,query_embedding));return deepcopy(self.rows)
    async def get_chunks(self,ids,context):
        self.chunk_calls.append(tuple(ids));return {id:deepcopy(self.chunks[id]) for id in ids if id in self.chunks}
    async def get_chunk_children(self,ids,context):
        rows=[deepcopy(r) for r in self.chunks.values() if r.parent_chunk_id in ids and r.chunk_type in ('text','image_ocr','image_caption')]
        # Existing test text fixtures carry image info; model its stored current enabled child.
        for id in ids:
            if id in self.chunks and self.chunks[id].image_info:
                rows.append(row(id+'-image',chunk_type='image_ocr',parent_chunk_id=id,image_info=self.chunks[id].image_info))
        return rows
    async def get_faq_answers(self,ids,context):return {id:self.faqs[id] for id in ids if id in self.faqs}
class Model:
    def __init__(self,outputs):self.outputs=list(outputs);self.calls=[]
    async def chat(self,messages,options,context):
        self.calls.append((messages,options,context));v=self.outputs.pop(0)
        if isinstance(v,Exception):raise v
        return SimpleNamespace(content=v)
    async def rerank(self,query,documents,context):
        self.calls.append((query,documents,context));v=self.outputs.pop(0)
        if isinstance(v,Exception):raise v
        return SimpleNamespace(results=v)
class Registry:
    def __init__(self,model=None):self.model=model;self.lookups=[]
    async def chat(self,id,context):self.lookups.append(id);return self.model
    async def rerank(self,id,context):
        if self.model is None:raise RuntimeError('lookup failed')
        return self.model

@pytest.mark.parametrize('output,rewrite,intent,image',[
    ('{}','question evidence','',''),
    ('{"rewrite_query":" abc ","intent":" greeting "}','abc','greeting',''),
    ('wrapped {"query":"alias"} tail','alias','',''),
    ('{"rewrite_query":"","query":"ignored"}','question evidence','',''),
    ('{"question":"next","description":"image","ocr":"words"}','next','','image\n\n[OCR]\nwords'),
    ('{"image_desc":" words ","ocr_text":" words "}','question evidence','','words'),
    ('garbage','question evidence','old',''),
    ('[]','question evidence','old',''),
])
def test_structured_output(output,rewrite,intent,image):
    s=state(intent='old');parse_understanding(s,output)
    assert (s.rewrite_query,s.intent,s.image_description)==(rewrite,intent,image)
@pytest.mark.parametrize('intent,web,expected',[('',False,True),('kb_search',False,True),('clarification',False,True),('summarize',False,True),('greeting',False,False),('follow_up',False,False),('image_only',False,False),('web_search',False,False),('web_search',True,True),('unknown',False,True)])
def test_intent_source(intent,web,expected):assert needs_retrieval(intent,web)==expected

def test_disabled_rewrite_does_not_call_model():
    reg=Registry(Model([]));s=state();run(understand_query(s,CTX,reg));assert not reg.lookups;assert s.rewrite_query==s.query

def test_images_activate_vision_with_source_budget_and_history():
    model=Model(['{"intent":"image_only","image_desc":"description"}']);reg=Registry(model)
    s=state(images=('image-handle',),chat_model_id='vision',chat_model_supports_vision=True,
            history=[SimpleNamespace(query='previous',answer='answer')])
    run(understand_query(s,CTX,reg));messages,options,context=model.calls[0]
    assert messages[1].images==('image-handle',)
    assert options.max_completion_tokens==500 and options.temperature==.3 and options.thinking is False
    assert 'count="1"' in messages[1].content and '------BEGIN------' in messages[0].content
    assert context.request_id=='r/query_rewrite'

def test_understand_model_error_retains_original():
    s=state(enable_rewrite=True,chat_model_id='text');m=Model([RuntimeError('safe mock')]);run(understand_query(s,CTX,Registry(m)))
    assert s.rewrite_query==s.query and not s.intent

def test_expansion_local_bytes_dedup_maxfive():
    assert expand_queries('How does retrieval work, with evidence?', 'same')==['retrieval work evidence','retrieval','evidence']
    assert expand_queries('"\u4e2d"', 'original',default_segmenter())==['\u4e2d']
    assert len(expand_queries('alphabeta; longsegmentone; longsegmenttwo; longsegmentthree; longsegmentfour; longsegmentfive','original'))==5
    assert expand_queries('ALPHA BETA','alpha beta')==[]

def test_han_missing_segmenter_explicit():
    with pytest.raises(TokenizerUnavailable):tokenize('\u4e2d\u6587')

@pytest.mark.parametrize('count,expected',[(0,True),(1,True),(2,False)])
def test_expansion_gate(count,expected):
    repo=Repo([row(str(i),f'unique original content {i}') for i in range(count)])
    s=state(embedding_top_k=2);s.enable_expansion=True;s.rewrite_query='How alpha beta';s.query='unrelated original'
    if count:run(search(s,CTX,repo))
    else:
        with pytest.raises(SearchNothing):run(search(s,CTX,repo))
    assert (len(repo.calls)>1)==expected
    if expected:
        assert repo.calls[1][1].match_count==60 and repo.calls[1][1].keyword_threshold==pytest.approx(.24)
        assert repo.calls[1][1].skip_context_enrichment

def test_scope_before_call_and_bad_result_fail_closed():
    repo=Repo();s=state();s.targets=(SearchTarget('other'),)
    with pytest.raises(RetrievalScopeError):run(search(s,CTX,repo))
    assert not repo.calls
    repo=Repo([replace(row(),knowledge_base_id='other')]);s=state()
    with pytest.raises(RetrievalScopeError):run(search(s,CTX,repo))

def test_document_scope_never_widens():
    ctx=replace(CTX,scope=TrustedScope('owner',('kb',),('doc',)))
    with pytest.raises(RetrievalScopeError):run(search(state(),ctx,Repo([row()])))
    s=state();s.targets=(SearchTarget('kb','knowledge',('doc',)),);run(search(s,ctx,Repo([row()])))

def test_embedding_group_once_and_keyword_only_degrade():
    ctx=replace(CTX,scope=TrustedScope('owner',('kb','kb2')))
    s=state();s.targets=(replace(TARGET,embedding_model_key='same'),SearchTarget('kb2',embedding_model_key='same'))
    counts=[]
    async def embed(key,query,context):counts.append(key);return [1.,2.]
    repo=Repo([row()]);run(search(s,ctx,repo,embedding_resolver=embed));assert counts==['same'];assert len(repo.calls)==1;assert repo.calls[0][3]==[1.,2.]
    async def fail(*args):raise RuntimeError('failed')
    repo=Repo([row()]);run(search(s,ctx,repo,embedding_resolver=fail));assert repo.calls[0][1].disable_vector
    s.targets=(replace(TARGET,embedding_model_key='same',keyword_index_enabled=False),)
    with pytest.raises(SearchError):run(search(s,ctx,Repo(),embedding_resolver=fail))

def test_parallel_target_calls_overlap():
    async def exercise():
        entered=0;event=asyncio.Event()
        class Parallel(Repo):
            async def hybrid_search(self,targets,params,context,query_embedding=None):
                nonlocal entered
                entered+=1
                if entered==2:event.set()
                await asyncio.wait_for(event.wait(),.2)
                return [row(str(entered),content=f'content {targets[0].tag_ids}')]
        s=state();s.targets=(replace(TARGET,tag_ids=('a',)),replace(TARGET,tag_ids=('b',)))
        await search(s,CTX,Parallel())
        assert entered==2
    run(exercise())

def test_dedup_and_overlap_shorter_high_score_survives():
    assert len(deduplicate([row('a','same BODY'),row('b',' same   body ')]))==1
    short=row('short','specific content evidence',.9);long=row('long','prefix specific content evidence suffix',.8)
    assert [r.id for r in partial_overlap([short,long])]==['short']

@pytest.mark.parametrize('score,accepted',[(.149,False),(.15,True),(.3,True)])
def test_rerank_top_one_floor(score,accepted):
    s=state(rerank_model_id='rerank');s.search_results=[row()];m=Model([[RerankResult(0,score)]])
    if accepted:run(rerank(s,CTX,Registry(m)));assert s.rerank_results
    else:
        run(rerank(s,CTX,Registry(m)));assert not s.rerank_results and s.search_results
        assert s.evidence_trace[-1]['reason']=='RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'

def test_rerank_retry_restores_threshold_and_override_floor():
    s=state(rerank_model_id='r',rerank_threshold=.8);s.search_results=[row()]
    m=Model([[RerankResult(0,.1)],[RerankResult(0,.4)]])
    run(rerank(s,CTX,Registry(m)));assert len(m.calls)==2 and s.rerank_threshold==.8
    s=state(rerank_model_id='r');s.search_results=[row()];s.targets=(replace(TARGET,disable_recall_thresholds=True),)
    run(rerank(s,CTX,Registry(Model([[RerankResult(0,0.)]]))));assert s.rerank_results

def test_rerank_api_failure_vs_lookup_failure():
    s=state(rerank_model_id='r');s.search_results=[row()];run(rerank(s,CTX,Registry(Model([RuntimeError()]))));assert s.search_results and not s.rerank_results
    with pytest.raises(RuntimeError,match='GET_RERANK_MODEL_FAILED'):run(rerank(s,CTX,Registry()))

def test_rerank_composite_boost_mmr_and_enrichment():
    s=state(rerank_model_id='r',faq_priority_enabled=True,faq_score_boost=2);s.search_results=[row(chunk_type='faq')]
    m=Model([[RerankResult(0,.5)]]);run(rerank(s,CTX,Registry(m)));assert s.rerank_results[0].score==1 and s.rerank_results[0].metadata['base_score']=='0.8000'
    r=row(content='# Title\n```py\ncode()\n```\n[linked](https://x) ![image](url)',image_info='[{"caption":"caption","ocr_text":"ocr"}]',chunk_metadata={'generated_questions':[{'question':'alias','content_revision':0}]})
    assert enriched_passage(r)=='Title\ncode()\nlinked\n\ncaption\nocr\nalias'
    rows=[row('a','duplicate tokens',.9),row('b','duplicate tokens',.85),row('c','diverse answer',.8)]
    assert [r.id for r in mmr(rows,[r.content for r in rows],2)]==['a','c']

@pytest.mark.parametrize('revision,rewritten',[(0,False),(1,False),(0,True)])
def test_merge_current_text_never_slices_edited_body(revision,rewritten):
    a=row('a','old body source',chunk_index=0,start_at=0,end_at=15,content_revision=revision,content_rewritten=rewritten)
    b=row('b','NEW EDITED CURRENT BODY',chunk_index=1,start_at=10,end_at=20,content_revision=revision,content_rewritten=rewritten)
    assert 'NEW EDITED CURRENT BODY' in sequential([a,b])[0].content

def test_merge_exact_trusted_overlap_retains_periodic_text():
    a=row('a','ABCABCABCABC',chunk_index=0,start_at=0,end_at=12)
    b=row('b','ABCABCABCABC',chunk_index=1,start_at=9,end_at=21)
    out=sequential([a,b]);assert out[0].content=='ABCABCABCABCABCABCABC' and out[0].end_at==21
    assert out[0].sub_chunk_ids==['b']

def test_parent_image_ocr_preserves_recognized_text_and_scopes_images():
    base=row('text','![hit](u) ![sibling](v)',parent_chunk_id='parent',image_info='[{"url":"u","ocr_text":"recognized text"}]')
    parent=row('parent','before ![hit](u) ![sibling](v) after',chunk_type='parent_text')
    hit=row('image','recognized text',chunk_type='image_ocr',parent_chunk_id='text')
    repo=Repo(chunks={'text':base,'parent':parent});s=state()
    out=run(resolve_parents([hit],s,CTX,repo))[0]
    assert 'recognized text' in out.content and '![hit](u)' in out.content and '![sibling]' not in out.content
    assert '"ocr_text": ""' in out.image_info and out.content_rewritten and out.sub_chunk_ids==['image']

def test_parent_scope_poison_is_hard_error():
    hit=row(parent_chunk_id='p');repo=Repo(chunks={'p':replace(row('p'),knowledge_base_id='other')})
    with pytest.raises(RetrievalScopeError):run(resolve_parents([hit],state(),CTX,repo))

def test_neighbors_same_document_and_850_bound():
    base=row('b','B'*10,pre_chunk_id='p',next_chunk_id='n')
    prev=row('p','P'*500);nxt=row('n','N'*500)
    repo=Repo(chunks={'b':base,'p':prev,'n':nxt});out=[deepcopy(base)]
    # Phase01 intentionally replaces the old slice-to-850 behavior:
    # assert len(out[0].content)==850 and out[0].sub_chunk_ids==['p','n']
    # An omitted neighbor must not be represented as fully included evidence.
    run(expand_neighbors(out,state(),CTX,repo))
    assert out[0].content=='P'*500+'\n\n'+'B'*10 and out[0].sub_chunk_ids==['p']
    assert len(out[0].content)<=850
    wrong=replace(nxt,knowledge_id='another');repo=Repo(chunks={'b':base,'n':wrong});out=[deepcopy(base)]
    run(expand_neighbors(out,state(),CTX,repo));assert out[0].content=='B'*10

def test_faq_current_answers_and_history_filter_discount():
    faq=row('faq',chunk_type='faq');repo=Repo(chunks={'faq':faq},faqs={'faq':{'question':' actual? ','answers':[' first ','','second']}})
    out=[faq];run(populate_faq(out,state(),CTX,repo));assert faq.content=='Q: actual?\nAnswer:\n- first\n- second'
    refs=[row(str(i),'question evidence',1.) for i in range(5)]
    s=state(history=[SimpleNamespace(knowledge_references=refs)]);out=inject_history([],s)
    assert len(out)==3 and all(r.score==.6 and r.match_type=='history' for r in out)

@pytest.mark.parametrize('text',['a\x00b','<script>x</script>','javascript:bad','onerror = bad','\ud800'])
def test_render_input_validation(text):
    with pytest.raises(ValueError):validate_input(text)

def test_render_faq_header_original_query_and_metadata_escaping():
    s=state(faq_priority_enabled=True,faq_direct_answer_threshold=.9);s.rewrite_query='REWRITE'
    s.merge_results=[row('f','answer',.95,chunk_type='faq',knowledge_title='title<&"'),row('d','document')]
    out=run(into_chat_message(s))
    assert '<context id="FAQ-1" match="exact">answer</context>' in out.rendered_contexts
    assert '<context id="DOC-1">document</context>' in out.rendered_contexts
    assert 'title&lt;&amp;&#34;' in out.rendered_contexts
    assert out.user_content.endswith('question evidence') and 'REWRITE' not in out.user_content

def test_image_metadata_spliced_once_against_original():
    r=row(content='![a](u)<img src="u">',image_info='[{"url":"u","caption":"![metadata](u)","ocr_text":"line1\\nline2"}]')
    value=enriched_chat_passage(r)
    assert value.count('**Image caption:**')==2 and '> line2' in value

def test_no_search_uses_rewrite_fallback_and_placeholders():
    s=state(needs_retrieval=False);s.rewrite_query='new question';s.context_template='{{query}} {{current_time}} {{unknown}}'
    out=run(into_chat_message(s,now=datetime(2026,10,13)));assert out.user_content=='new question 2026-10-13 {{unknown}}'
    s.rewrite_query='<script>x</script>';out=run(into_chat_message(s));assert out.user_content.startswith('question evidence')

def test_formal_pipeline_finalizes_before_model_role_and_topk():
    repo=Repo([row('b','second independent material',.6),row('a','first evidence information',.9)])
    s=state(rerank_top_k=1);out=run(RetrievalPipeline(repo,Registry()).run(s,CTX))
    assert out.result_ids==('a',) and '<context id="1">' in out.rendered_contexts
    assert len(repo.calls)==1 and out.user_content==s.user_content


def test_image_child_collection_two_levels_disabled_and_merge_by_url():
    from backend.weknora_port.retrieval.merge import collect_image_info
    text=row('text',parent_chunk_id='parent')
    ocr=row('ocr',chunk_type='image_ocr',parent_chunk_id='text',image_info='[{"url":"u","ocr_text":"recognized"}]')
    caption=row('caption',chunk_type='image_caption',parent_chunk_id='text',image_info='[{"url":"u","caption":"caption"}]')
    disabled=row('disabled',chunk_type='image_ocr',parent_chunk_id='text',image_info='[{"url":"v","ocr_text":"removed"}]',is_enabled=False)
    result=run(collect_image_info(['parent'],state(),CTX,Repo(chunks={r.id:r for r in (text,ocr,caption,disabled)})))
    import json
    assert len(json.loads(result['parent']))==1
    assert json.loads(result['parent'])[0]['url']=='u' and json.loads(result['parent'])[0]['ocr_text']=='recognized' and json.loads(result['parent'])[0]['caption']=='caption'

def test_legacy_history_helper_is_preserved_but_active_merge_ignores_it():
    from dataclasses import asdict
    s=state(history=[SimpleNamespace(knowledge_references=[asdict(row('history','question evidence',1.))])])
    assert inject_history([],s)[0].score==.6
    s.history[0].knowledge_references[0]['knowledge_base_id']='other'
    # S8.5: historical source rows never enter the current evidence chain.
    run(merge(s,CTX,None))
    assert s.merge_results == []

def test_global_cancel_and_deadline_are_not_model_timeouts():
    from backend.weknora_port.models.types import CallCancelled,RunDeadlineExceeded
    async def exercise():
        event=asyncio.Event();event.set()
        with pytest.raises(CallCancelled):await RetrievalPipeline(Repo(),Registry()).run(state(),replace(CTX,cancelled=event))
        with pytest.raises(RunDeadlineExceeded):await RetrievalPipeline(Repo(),Registry()).run(state(),replace(CTX,deadline=0,deadline_kind='run'))
    run(exercise())

def test_entity_result_does_not_disable_chunk_expansion():
    async def entity(s,c):return [row('entity','entity result document')]
    s=state(entities=('entity',),embedding_top_k=1);s.enable_expansion=True;s.rewrite_query='How alpha beta'
    repo=Repo([]);run(search(s,CTX,repo,entity_search=entity))
    assert len(repo.calls)>1 and s.search_results[0].id=='entity'

def test_permission_failure_is_never_soft_fallback():
    class Denied(Repo):
        async def get_chunks(self,ids,context):raise PermissionError('scope denied')
    with pytest.raises(PermissionError):run(resolve_parents([row(parent_chunk_id='p')],state(),CTX,Denied()))

def test_html_data_src_is_not_actual_src():
    r=row(content='<img data-src="u" src="v">',image_info='[{"url":"u","caption":"wrong"},{"url":"v","caption":"correct"}]')
    out=enriched_chat_passage(r);assert 'correct' in out and 'wrong' not in out


def test_source_default_context_template_and_rewrite_blocks():
    from backend.weknora_port.retrieval.pipeline import DEFAULT_CONTEXT_TEMPLATE,DEFAULT_REWRITE_SYSTEM,DEFAULT_REWRITE_USER
    assert DEFAULT_CONTEXT_TEMPLATE=='## Reference materials (source data)\n{{contexts}}\n\n## Request metadata\nCurrent time: {{current_time}} {{current_week}}\n\n## User request\n{{query}}\n'
    assert '{{conversation}}' in DEFAULT_REWRITE_SYSTEM and 'THREE tasks' in DEFAULT_REWRITE_SYSTEM
    assert DEFAULT_REWRITE_USER.endswith('## JSON Output\n')
    assert PipelineState().context_template==DEFAULT_CONTEXT_TEMPLATE


def test_web_composite_uses_knowledge_source_not_match_label():
    s=state(rerank_model_id='r');s.search_results=[row(knowledge_source='WEB_SEARCH',match_type='vector')]
    run(rerank(s,CTX,Registry(Model([[RerankResult(0,.5)]]))))
    assert s.rerank_results[0].score==pytest.approx(.635)

def test_invalid_image_metadata_and_null_history_metadata_are_safe():
    assert enriched_chat_passage(row(image_info='[{"caption":123}]'))=='relevant document evidence'
    assert enriched_passage(row(chunk_metadata='[]'))=='relevant document evidence'
    s=state(history=[SimpleNamespace(knowledge_references=[row('history','question evidence',metadata=None)])])
    assert inject_history([],s)[0].metadata['history_similarity']=='1'
