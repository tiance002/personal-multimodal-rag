"""Pipeline/source-registration contracts plus unmodified saved R replay."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResponse, RerankResult
from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.rerank import rerank, enriched_passage, model_passage
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.types import SearchResult, context_results
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from test_bridge_rescue import case, Models, Repository, CTX, QUESTION, extraction

FIXTURES=Path(__file__).parent/"fixtures"/"s8_4_bridge"
INPUTS=json.loads((FIXTURES/"inputs.json").read_text(encoding="utf8"))
PROOF=json.loads((FIXTURES/"baseline-proof.json").read_text(encoding="utf8"))

class PipelineRepository(Repository):
    def __init__(self, rows):
        super().__init__(rows)
        self.search_calls=[]
    async def hybrid_search(self, targets, params, context, query_embedding=None):
        self.search_calls.append((targets,params))
        return deepcopy(list(self.rows.values()))
    async def get_chunks(self, ids, context):
        return await self.get_current_chunks(ids,context)
    async def get_chunk_children(self, ids, context):
        return []
    async def get_faq_answers(self, ids, context):
        return {}

class PipelineModels(Models):
    def __init__(self, content):
        super().__init__(content)
        self.initial_calls=[]
    async def rerank(self, model_id, context):
        if not context.request_id.endswith("/bridge-rerank"):
            return SimpleNamespace(rerank=self.initial_rank)
        return await super().rerank(model_id,context)
    async def initial_rank(self, query, documents, context):
        self.initial_calls.append((query,tuple(documents),context))
        return RerankResponse(tuple(RerankResult(i,.95 if i==0 else .02) for i in range(len(documents))))

def pipeline_case(mode="cheap_bridge_v1",limit=16000):
    base,_,_=case()
    repo=PipelineRepository(base.search_results)
    models=PipelineModels(json.dumps(extraction(base.search_results[0]),ensure_ascii=False))
    state=PipelineState(query=QUESTION,targets=(SearchTarget("kb"),),enable_rewrite=False,
        enable_expansion=False,rerank_model_id="bge",bridge_rescue_mode=mode,
        bridge_cheap_model_id="cheap-snapshot",bridge_context_char_limit=limit)
    return state,repo,models

def test_full_mock_chain_delivers_only_original_second_hop_to_generation():
    async def scenario():
        state,repo,models=pipeline_case()
        normal={}
        async def progress(stage,status,**fields):
            if stage=="merge" and status=="completed":
                normal["arrays"]=deepcopy((state.search_results,state.rerank_results,state.merge_results))
        finalized=await RetrievalPipeline(repo,models,segmenter=lambda text:text.split(),on_progress=progress).run(state,CTX)
        assert len(models.initial_calls)==len(models.chat_calls)==len(models.rerank_calls)==1
        assert len(repo.search_calls)==1
        assert any(t["stage"]=="rerank_filter_mmr" and t["accepted_ids"]==["first"] for t in state.evidence_trace)
        assert normal["arrays"]==(state.search_results,state.rerank_results,state.merge_results)
        assert finalized.result_ids==("first","second")
        assert repo.rows["second"].content in finalized.rendered_contexts
        evidence=tuple(Evidence.from_result(r) for r in context_results(state))
        assert evidence[1].source_content==repo.rows["second"].content
        assert evidence[1].start_at==repo.rows["second"].start_at
        prepared=prepare_generation(GenerationRequest("run","session","message",QUESTION,finalized,evidence=evidence))
        wire=prepared.full_prompt
        assert repo.rows["second"].content in wire
        assert 'institution_location_only' in wire and 'seminar_venue' in wire
        assert '"subject_resolution"' not in wire
        assert 'Where is Arbor Poetry Seminar located?' not in wire
        assert state.rescue_evidence[0].score==repo.rows["second"].score
    asyncio.run(scenario())

def test_off_mode_keeps_original_pipeline_and_extra_calls_absent():
    async def scenario():
        state,repo,models=pipeline_case("off")
        finalized=await RetrievalPipeline(repo,models,segmenter=lambda text:text.split()).run(state,CTX)
        assert finalized.result_ids==("first",)
        assert not state.rescue_evidence and not state.bridge_trace
        assert not state.evidence_trace
        assert len(models.initial_calls)==1 and not models.chat_calls and not models.rerank_calls
    asyncio.run(scenario())

def test_actual_retrieval_context_budget_drops_only_optional_source():
    async def scenario():
        ordinary,repo,models=pipeline_case("off")
        baseline=await RetrievalPipeline(repo,models,segmenter=lambda text:text.split()).run(ordinary,CTX)
        state,repo,models=pipeline_case(limit=len(baseline.rendered_contexts)+10)
        finalized=await RetrievalPipeline(repo,models,segmenter=lambda text:text.split()).run(state,CTX)
        assert finalized.result_ids==baseline.result_ids==("first",)
        assert state.merge_results and not state.rescue_evidence
        assert state.bridge_trace[-1]["reason"]=="BRIDGE_CONTEXT_BUDGET_EXCEEDED"
        assert len(models.chat_calls)==len(models.rerank_calls)==1
    asyncio.run(scenario())

def test_generation_context_budget_rechecks_final_source_encoder_before_routing():
    async def scenario():
        from test_service import runtime, REQ
        from backend.weknora_port.service import TurnConfiguration
        state,repository,models=pipeline_case()
        finalized=await RetrievalPipeline(repository,models,segmenter=lambda text:text.split()).run(state,CTX)
        state.bridge_context_char_limit=len(finalized.rendered_contexts)+1
        finalized=await into_chat_message(state,CTX)
        class PreparedPipeline:
            persist_sink=None
            async def run(self,state,context): return finalized
        service,repo,*_=runtime(configuration=lambda turn:TurnConfiguration(state,{}))
        service.pipeline_factory=lambda turn,progress:PreparedPipeline()
        turn=await repo.accept(replace(REQ,content=QUESTION),"owner")
        async def progress(*args,**kwargs): pass
        prepared=await service.prepare_turn(turn,progress)
        assert not state.rescue_evidence
        assert state.bridge_trace[-1]["reason"]=="BRIDGE_GENERATION_CONTEXT_BUDGET_EXCEEDED"
        assert prepared.request.finalized.result_ids==("first",)
        assert tuple(e.id for e in prepared.request.evidence)==("first",)
        assert dict(prepared.rankings)["context"]==(("first","kb","doc"),)
        prepare_generation(prepared.request)
    asyncio.run(scenario())

@pytest.mark.parametrize("saved",[c for c in INPUTS["cases"] if c["case_id"].startswith("frost-")],ids=lambda c:c["case_id"])
def test_saved_frost_first_rerank_loss_and_unverified_pronoun_are_not_forced_to_pass(saved):
    async def scenario():
        rows=[SearchResult(**r) if r else None for r in saved["search_results"]]
        state=PipelineState(query=saved["query"],rewrite_query=saved["rewrite_query"],
            targets=tuple(SearchTarget(k) for k in saved["knowledge_base_ids"]),
            enable_rewrite=False,enable_expansion=False,rerank_model_id="bge",
            bridge_rescue_mode="cheap_bridge_v1",bridge_cheap_model_id="cheap",
            search_results=deepcopy(rows))
        ctx=CallContext(saved["case_id"],TrustedScope("owner",tuple(saved["knowledge_base_ids"])))
        raw=saved["evidence_trace"][0]["response"]
        class Initial:
            async def rerank(self,*args):
                return RerankResponse(tuple(RerankResult(x["index"],x["score"]) for x in raw))
        class RankRegistry:
            async def rerank(self,*args): return Initial()
        await rerank(state,ctx,RankRegistry(),lambda text:text.split())
        proof=next(x for x in PROOF["cases"] if x["case_id"]==saved["case_id"])
        assert len(rows)==30
        assert {r.id for r in state.rerank_results}==set(proof["accepted_before_mmr"])
        second=next(r for r in rows if r and r.id=="31417c7c-7abf-4723-956a-74bf7ee14683")
        assert second.id not in {r.id for r in state.rerank_results}
        score=next(x["score"] for x in raw if rows[x["index"]].id==second.id)
        assert score<.3
        # Replay the already verified S8.3 normal source projection; do not
        # replace it with an idealized source or insert the missing hop.
        state.merge_results=[SearchResult(**r) for r in saved["merge_results"]]
        first=next(r for r in rows if r and r.id=="ec07a1fd-8a8e-4f23-a7d6-65d385d2343b")
        quote=first.content[155:311]
        assert quote.startswith("She served as the Director") and quote.endswith("Writing.")
        data=dict(status="FIRST_HOP_CANDIDATE",source_chunk_id=first.id,quote_start=155,quote_end=311,
            quote=quote,subject="She",subject_resolution="UNVERIFIED",predicate="served as the Director of",
            object="The Frost Place Advanced Seminar",time="2007–2010",target_attribute="location",
            bridge_search_phrases=["The Frost Place Advanced Seminar","The Frost Place"])
        models=Models(json.dumps(data,ensure_ascii=False))
        repository=Repository([SearchResult(**r) for r in saved["source_rows"]])
        before=deepcopy((state.search_results,state.rerank_results,state.merge_results))
        await rescue_bridge(state,ctx,repository,models)
        assert before==(state.search_results,state.rerank_results,state.merge_results)
        assert len(models.chat_calls)==1 and not models.rerank_calls and not state.rescue_evidence
        assert any(x.get("decision")=="FIRST_HOP_SPAN_VERIFIED" for x in state.bridge_trace)
        assert state.bridge_trace[-1]["reason"]=="SUBJECT_IDENTITY_UNVERIFIED"
        finalized=await into_chat_message(state,ctx)
        assert second.id not in finalized.result_ids and second.content not in finalized.rendered_contexts
    asyncio.run(scenario())

@pytest.mark.parametrize("saved",[c for c in INPUTS["cases"] if not c["case_id"].startswith("frost-")],ids=lambda c:c["case_id"])
def test_saved_negative_controls_make_no_bridge_calls(saved):
    state=PipelineState(query=saved["query"],rewrite_query=saved["rewrite_query"],
        targets=tuple(SearchTarget(k) for k in saved["knowledge_base_ids"]),
        bridge_rescue_mode="cheap_bridge_v1",bridge_cheap_model_id="cheap",rerank_model_id="bge",
        search_results=[SearchResult(**r) if r else None for r in saved["search_results"]],
        rerank_results=[SearchResult(**r) for r in saved["rerank_results"]],
        merge_results=[SearchResult(**r) for r in saved["merge_results"]],
        evidence_trace=deepcopy(saved["evidence_trace"]))
    ctx=CallContext(saved["case_id"],TrustedScope("owner",tuple(saved["knowledge_base_ids"])))
    models=Models('{"status":"NO_FIRST_HOP"}')
    repository=Repository([SearchResult(**r) for r in saved["source_rows"]])
    before=deepcopy(state.merge_results)
    asyncio.run(rescue_bridge(state,ctx,repository,models))
    assert not models.chat_calls and not models.rerank_calls and not state.rescue_evidence
    assert state.merge_results==before

@pytest.mark.parametrize("mode",[True,"on","cheap_bridge_v2",None])
def test_runtime_experiment_mode_is_strict_and_server_owned(mode):
    from backend.weknora_port.runtime import ConversationSettings
    with pytest.raises(ValueError,match="BRIDGE_RESCUE_MODE_INVALID"):
        ConversationSettings(bridge_rescue_mode=mode)
