import asyncio
from dataclasses import replace
import httpx
import pytest
from backend.weknora_port.models import provider_config, ProviderError, ChatOptions, Message
from backend.weknora_port.models.usage import normalize_usage,accumulate_usage
from backend.weknora_port.models.provider import parse_chat_response
from tests.weknora_port.test_provider_transport import setup,context,response


@pytest.mark.parametrize("fields,read,write,miss",[
    ({"prompt_cache_hit_tokens":4,"prompt_cache_miss_tokens":6},4,0,6),
    ({"cache_read_input_tokens":4,"cache_creation_input_tokens":2},4,2,6),
    ({"prompt_tokens_details":{"cached_tokens":4,"cache_write_tokens":2}},4,2,6),
    ({"cached_tokens":4},4,0,6),
])
def test_source_cache_variants_do_not_double_count_prompt(fields,read,write,miss):
    u=normalize_usage({"prompt_tokens":10,"completion_tokens":2,**fields},True)
    assert u.prompt_tokens==10 and u.total_tokens==12 and u.status=="REPORTED"
    assert (u.cache_read_tokens,u.cache_write_tokens,u.cache_miss_tokens)==(read,write,miss)


def test_unknown_cache_unreported_and_supported_cache_zero_miss():
    assert normalize_usage(None,True).cache_status=="unreported"
    assert normalize_usage(None,False).cache_status=="unsupported"
    partial=normalize_usage({"prompt_tokens":3})
    assert partial.completion_tokens is None and partial.status=="UNKNOWN"
    reported=normalize_usage({"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"cached_tokens":0})
    assert reported.status=="REPORTED" and reported.total_tokens==0 and reported.cache_status=="miss"
    assert accumulate_usage([reported,partial]).status=="UNKNOWN"


def test_nonstream_inline_think_truncation_empty_answer_and_missing_model_source_behavior():
    cfg=provider_config("deepseek","deepseek-flash")
    env={"choices":[{"message":{"content":"<think>r</think> answer"},"finish_reason":"length"}]}
    out=parse_chat_response(env,cfg)
    assert out.content=="answer" and out.reasoning_content=="r" and out.truncated
    assert out.model=="" and out.usage.status=="UNKNOWN"
    env["choices"][0]["message"]["content"]="<think>unfinished"
    out=parse_chat_response(env,cfg)
    assert out.content=="" and out.reasoning_content=="unfinished"
    env["choices"][0]["message"]["content"]=""
    assert parse_chat_response(env,cfg).content==""  # quality signal belongs to P5


@pytest.mark.parametrize("rows,code",[
    ([{"index":0,"embedding":[1.]},{"index":0,"embedding":[2.]}],"INVALID"),
    ([{"index":0,"embedding":[1.]}],"MISSING"),
    ([{"index":9,"embedding":[1.]}],"INVALID"),
])
def test_embeddings_reject_duplicate_gap_and_wrong_index(rows,code):
    async def run():
        model,transport,_=setup(lambda req:httpx.Response(200,json={"data":rows}))
        try:
            with pytest.raises(ProviderError,match="PROVIDER_EMBEDDING_"+code):
                await model.embed(["a","b"],context())
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_embeddings_batch_order_positional_indices_and_independent_send_identities():
    async def run():
        cfg=provider_config("siliconflow","BAAI/bge-m3")
        cfg=replace(cfg,embedding=replace(cfg.embedding,max_batch_size=2))
        batches=[]
        def handler(req):
            import json
            texts=json.loads(req.content)["input"]
            batches.append(texts)
            return httpx.Response(200,json={"data":[{"embedding":[float(text)]} for text in texts]})
        model,transport,gate=setup(handler,config=cfg)
        try:
            out=await model.embed(["1","2","3"],context())
            assert out.vectors==((1.,),(2.,),(3.,)) and batches==[["1","2"],["3"]]
            assert [c.context.request_id for c in gate.calls]==["request/embedding-0","request/embedding-1"]
            assert out.usage.status=="UNKNOWN"
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_rerank_score_alias_text_shapes_and_batch_global_indices():
    async def run():
        cfg=provider_config("siliconflow","BAAI/bge-reranker-v2-m3")
        cfg=replace(cfg,reranking=replace(cfg.reranking,max_documents=1))
        model,transport,gate=setup(lambda req:httpx.Response(200,json={"results":[{"index":0,"score":.7,"document":{"text":"doc"}}]}),config=cfg)
        try:
            out=await model.rerank("q",["one","two"],context())
            assert [(r.index,r.score,r.text) for r in out.results]==[(0,.7,"doc"),(1,.7,"doc")]
            assert len(gate.calls)==2 and out.score_scale=="probability"
        finally:
            await transport.aclose()
    asyncio.run(run())
