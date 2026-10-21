"""P18 only: frozen generation A/B through existing Registry/Authority/P1 gate."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from scripts.weknora_port_p13_rerank import wire, require, exclusive, claim
from scripts.weknora_port_p17_observed_api import RawResponse, journal

ENHANCEMENT = '''
- Citation output check: match each source-based factual sentence or list item to the current evidence before finishing. Place a legal <ref id="cN"/> or <ref id="wN"/> immediately beside the claim it actually supports, using only a current citable handle.
- A citation must support the whole adjacent claim. Split or qualify a mixed claim; do not use one source citation to imply support for unsupported additions. Preserve the user's exact-format restrictions above.
- If the supplied evidence does not support a claim, omit or explicitly qualify it without a fabricated citation. If no supplied evidence answers the question, state the evidence limitation without inventing a source. Do not output public <kb>/<web> tags or bare private handles.
'''.rstrip()
BOUNDARY = '\n\n## Resource handle protocol'


def enhanced(system):
    require(system.count(BOUNDARY) == 1 and 'Source citations are enabled' in system, 'P18_PROTOCOL_BOUNDARY')
    return system.replace(BOUNDARY, ENHANCEMENT + BOUNDARY)


def registry_from(row):
    from backend.weknora_port.references import SourceRegistry, ChunkReference
    require(row['citable'] == "frozenset({'c1'})" and not row['webs'] and not row['resources'], 'P18_FIXED_REGISTRY_SCOPE')
    registry = SourceRegistry()
    for handle, fields in row['chunks']:
        require(registry.register_chunk(ChunkReference(*fields), evidence=True) == handle, 'P18_HANDLE_CHANGED')
    for handle, doc in row['documents']:
        require(registry.register_document(doc) == handle, 'P18_DOCUMENT_HANDLE_CHANGED')
    for handle, kb in row['knowledge_bases']:
        require(registry.register_knowledge_base(kb) == handle, 'P18_KB_HANDLE_CHANGED')
    return registry


def validate(plan):
    require(len(plan['cases']) == 4 and len({c['case_id'] for c in plan['cases']}) == 4, 'P18_FOUR_CASES')
    require(len({c['request_id'] for c in plan['cases']}) == 4, 'P18_REQUEST_IDENTITIES')
    for q in plan['questions']:
        cases = [c for c in plan['cases'] if c['qid'] == q['qid']]
        require([c['arm'] for c in cases] == ['A', 'B'], 'P18_PAIR_REQUIRED')
        a, b = cases
        require(a['body'] == q['original_body'], 'P18_A_NOT_ORIGINAL')
        expected = json.loads(json.dumps(a['body']))
        expected['messages'][0]['content'] = enhanced(expected['messages'][0]['content'])
        require(b['body'] == expected, 'P18_NON_PROTOCOL_VARIABLE_CHANGED')
        require(len(a['body']['messages']) == 2 and a['body']['messages'][0]['role'] == 'system', 'P18_MESSAGE_SHAPE')
        registry_from(q['registry'])
    for c in plan['cases']:
        raw = wire(c['body'])
        require(len(raw) == c['wire_bytes'] and hashlib.sha256(raw).hexdigest() == c['wire_sha256'], 'P18_WIRE_CHANGED')
    require(sum(c['wire_bytes'] + c['body']['max_tokens'] for c in plan['cases']) <= 100000, 'P18_FENCE_TOO_SMALL')


async def execute(path):
    from scripts.weknora_port_p8_real import dsn
    from backend.weknora_port.authority import load_authority_settings
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.models import Message, ChatOptions, CallContext, TrustedScope
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.references import CitationStreamDecoder
    from scripts.weknora_port_p14_rerank import binding
    p = json.loads(path.read_text(encoding='utf-8')); validate(p); root = path.parent
    require(root.resolve() == Path('E:/WK/run/p18-20261016').resolve(), 'P18_OUTPUT_ROOT')
    authority = root/'authority.json'
    require(hashlib.sha256(authority.read_bytes()).hexdigest() == p['authority_sha256'], 'P18_AUTHORITY_CHANGED')
    cfg = load_authority_settings(authority); s = p['runtime_profile']
    require(cfg.limit_tokens == 1568295 and cfg.allowed_models == (('siliconflow','XingChenAGI/Xing4.0-29B'),), 'P18_AUTHORITY_SCOPE')
    settings = DevelopmentSettings(dsn(), '7696088403504697382', 'weknora_python_port_20261013',
        'redis://127.0.0.1:56424/15', s['redis_run_id'], s['redis_prefix'], Path(s['storage_root']),
        Path(s['secret_key_file']), Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken'), router_mode='CHEAP_ONLY', fallback_strategy='fixed')
    async with assemble(settings, authority_settings=cfg) as app:
        scope = TrustedScope(app.principal.subject_id, (p['kb_id'],), ())
        registry = app.registry_for_context()
        try:
            model = await registry.chat(p['model_id'], CallContext('p18-preflight', scope))
            require((model.config.provider, model.config.model) == ('siliconflow','XingChenAGI/Xing4.0-29B'), 'P18_MODEL_CHANGED')
            current = {}; original_open = model.transport.open
            @asynccontextmanager
            async def observed(config, endpoint, body, ctx, **kwargs):
                require(kwargs['capability'] == 'chat' and current.get('sends') == 0, 'P18_EXTRA_CALL_DENIED')
                require(wire(body) == wire(current['case']['body']), 'P18_SEND_BODY_CHANGED')
                current['sends'] = 1
                async with original_open(config, endpoint, body, ctx, **kwargs) as handle:
                    raw = RawResponse(handle.response, root, ctx.request_id, 'chat'); handle.response = raw
                    try: yield handle
                    finally: raw.finish()
            model.transport.open = observed
            async def actual_wire_guard(request):
                require(hashlib.sha256(request.content).hexdigest() == current['case']['wire_sha256'], 'P18_ACTUAL_WIRE_CHANGED')
                exclusive(current['dir']/'request.body.json', json.loads(request.content))
            model.transport.client.event_hooks['request'].insert(0, actual_wire_guard)
            for case in p['cases']:
                kb = await app._rows('SELECT tenant_id,cloud_allowed,deleted_at FROM knowledge_bases WHERE id=%s', (p['kb_id'],))
                require(kb and kb[0]['tenant_id'] == 332 and kb[0]['cloud_allowed'] and not kb[0]['deleted_at'], 'P18_KB_PERMISSION_CHANGED')
                docs = await app._rows('SELECT id,metadata,parse_status,deleted_at FROM knowledges WHERE knowledge_base_id=%s', (p['kb_id'],))
                versions = {r['id']:(json.loads(r['metadata']) if isinstance(r['metadata'],str) else r['metadata'])['source_version'] for r in docs if not r['deleted_at'] and r['parse_status']=='completed'}
                require(versions == p['document_versions'], 'P18_DOCUMENT_CHANGED')
                chunks = await app._rows('SELECT * FROM chunks WHERE knowledge_base_id=%s AND tenant_id=332', (p['kb_id'],))
                byid = {r['id']:r for r in chunks if not r['deleted_at']}
                require(all(i in byid and binding(byid[i]) == h for i,h in p['source_chunk_bindings'].items()), 'P18_SOURCE_CHANGED')
                q = next(q for q in p['questions'] if q['qid'] == case['qid'])
                body = case['body']; messages = tuple(Message(m['role'],m['content']) for m in body['messages'])
                opts = ChatOptions(temperature=body['temperature'],max_tokens=body['max_tokens'],reasoning_effort='off')
                require(build_chat_body(model.config,messages,opts,stream=True) == body, 'P18_RENDERED_REQUEST_CHANGED')
                directory = claim(root,case); current.clear(); current.update(case=case,dir=directory,sends=0)
                citations = registry_from(q['registry']); decoder = CitationStreamDecoder(citations)
                exclusive(directory/'registry.json', asdict(citations.snapshot()))
                raw_text=[]; answer=[]; events=[]; started=time.monotonic(); finish=''
                ctx=CallContext(case['request_id'],scope,deadline=asyncio.get_running_loop().time()+300,deadline_kind='run')
                try:
                    async for event in model.chat_stream(messages,opts,ctx):
                        row=asdict(event); events.append(row); journal(root,ctx.request_id,{'kind':'adapter_event',**row})
                        if event.finish_reason: finish=event.finish_reason
                        if event.response_type == 'error': raise RuntimeError('P18_PROVIDER_ERROR_NO_RETRY')
                        if event.response_type == 'answer':
                            raw_text.append(event.content); out=decoder.feed(event.content); answer.append(out)
                            journal(root,ctx.request_id,{'kind':'decoder_feed','input':event.content,'output':out,'used':sorted(citations.used_handles),'invalid':citations.invalid_count})
                    tail=decoder.flush(); answer.append(tail)
                    exclusive(directory/'events.json',events)
                    exclusive(directory/'result.json',dict(status='RECORDED',raw_answer=''.join(raw_text),decoded_answer=''.join(answer),finish_reason=finish,
                        used_handles=sorted(citations.used_handles),invalid_count=citations.invalid_count,flush_tail=tail,seconds=time.monotonic()-started,physical_sends=current['sends']))
                    print(case['case_id'],'RECORDED',flush=True)
                except BaseException as error:
                    exclusive(directory/'failure.json',dict(type=type(error).__name__,safe_code=getattr(error,'code',''),no_retry=True,physical_sends=current['sends']))
                    raise
        finally: await registry.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True);p.add_argument('--validate-only',action='store_true');a=p.parse_args()
    if a.validate_only:
        validate(json.loads(a.frozen.read_text(encoding='utf-8')));print('P18_FROZEN4_VALID');return
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(execute(a.frozen))


if __name__=='__main__':main()
