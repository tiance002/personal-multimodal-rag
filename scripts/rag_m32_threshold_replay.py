"""Offline saved-response threshold replay through the unchanged product functions.

No sockets, provider requests or database writes. Snapshot export is a separate,
explicitly read-only operation. Optional --labels JSON maps case -> chunk id ->
manual relevance label; absent labels remain UNKNOWN, never inferred from score.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, importlib, json, math, socket, sys
from copy import deepcopy
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace as NS

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.context import into_chat_message, enriched_chat_passage
rr = importlib.import_module('backend.weknora_port.retrieval.rerank')

def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def textsha(s): return hashlib.sha256(s.encode()).hexdigest()
def blocked(*args, **kwargs): raise RuntimeError('OFFLINE_NETWORK_FORBIDDEN')

def disable_network():
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked


def snapshot_differences(historical, current):
    if current is None:
        return ['MISSING']
    differences = [name for name in ('content', 'knowledge_base_id', 'knowledge_id', 'chunk_type')
                   if getattr(historical, name) != getattr(current, name)]
    if historical.chunk_metadata.get('source_version') != current.chunk_metadata.get('source_version'):
        differences.append('source_version')
    return differences


def validate_raw_results(raw, candidate_count):
    if not isinstance(raw, list):
        raise ValueError('INVALID_RAW_RESULTS_TYPE')
    seen = set()
    for row in raw:
        if not isinstance(row, dict) or type(row.get('index')) is not int:
            raise ValueError('INVALID_RAW_INDEX_TYPE')
        index = row['index']
        if not 0 <= index < candidate_count:
            raise ValueError('RAW_INDEX_OUT_OF_RANGE:' + str(index))
        if index in seen:
            raise ValueError('DUPLICATE_RAW_INDEX:' + str(index))
        seen.add(index)
        score = row.get('relevance_score')
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError('INVALID_OR_MISSING_RAW_SCORE:' + str(index))


def replay_verdict(cases, baseline_all_match):
    incomplete = [
        {'case':case['case'], 'threshold':run['threshold'],
         'merge_status':run.get('merge_status', 'MISSING')}
        for case in cases for run in case['threshold_runs']
        if run['status'] != 'SEARCH_NOTHING' and
           (run.get('merge_status') != 'PASS' or
            'rendered_contexts' not in run or 'through_top_k_ids' not in run)
    ]
    if not baseline_all_match:
        return 'FAIL_BASELINE_MISMATCH', 1, incomplete
    if incomplete:
        return 'PARTIAL_NOT_REPLAYABLE', 2, incomplete
    return 'PASS_OFFLINE_PRODUCT_REPLAY', 0, incomplete


class SavedRepository:
    def __init__(self, rows): self.rows = rows; self.requested = set()
    @staticmethod
    def check_scope(rows, context):
        scope = context.scope
        for row in rows:
            if row.knowledge_base_id not in scope.knowledge_base_ids or (scope.document_ids and row.knowledge_id not in scope.document_ids):
                raise PermissionError('SNAPSHOT_SCOPE_IDENTITY_MISMATCH:' + row.id)

    async def get_chunks(self, ids, context):
        self.requested.update(ids)
        missing = set(ids) - self.rows.keys()
        if missing: raise PermissionError('MISSING_SNAPSHOT_ROWS:' + ','.join(sorted(missing)))
        self.check_scope([self.rows[i] for i in ids], context)
        return {i: deepcopy(self.rows[i]) for i in ids}
    async def get_chunk_children(self, ids, context):
        self.requested.update(ids)
        rows = [r for r in self.rows.values() if r.parent_chunk_id in ids and r.is_enabled]
        self.check_scope(rows, context)
        return deepcopy(rows)
    async def get_faq_answers(self, ids, context):
        rows = await self.get_chunks(ids, context)
        return {i: row.chunk_metadata for i, row in rows.items()}

async def main(args):
    # Fail closed even if an accidental future adapter attempts a network call.
    disable_network()
    root, evidence = args.source, args.source/'evidence-332'
    provenance = {}
    def load(p): provenance[str(p)] = sha(p); return read(p)
    stages = {d['run_id']:(p,d) for p in (evidence/'stages').glob('*.json') for d in [load(p)]}
    wires = {d['run_id'].removesuffix('/rerank'):(p,d) for p in (evidence/'provider-requests').glob('*.json') for d in [load(p)] if d.get('capability')=='rerank'}
    snapshot = load(args.snapshot) if args.snapshot else None
    rows = {r['id']:SearchResult(**r) for r in snapshot['rows']} if snapshot else {}
    focus = load(args.focus)
    focusmap = {r['case']:{x['chunk_id']:x['chunk_type'] for x in r['focus']} for r in focus['cases']}
    labels = load(args.labels) if args.labels else {}
    output = {'schema':'rag-m32-threshold-product-replay/v1','mode':'OFFLINE_SAVED_REAL_RESPONSES',
              'cloud_calls':0,'database_writes':0,'snapshot_status':snapshot['status'] if snapshot else 'MISSING',
              'thresholds':args.thresholds,'parameters':{},'cases':[]}
    defaults = PipelineState()
    for name in ['rerank_threshold','rerank_top_k','faq_priority_enabled','faq_score_boost','context_template']:
        output['parameters'][name]={'value':getattr(defaults,name),'provenance':'PipelineState/SearchState dataclass defaults; historical capture omits these where absent'}
    segmenter = default_segmenter()
    for resultpath in sorted((root/'cases').glob('*/result.json')):
        result = load(resultpath); case = resultpath.parent.name; run = result.get('run_id')
        if run not in stages: continue
        stagepath, stage = stages[run]
        completed = {s['stage']:s for s in stage['stages'] if s['status']=='completed'}
        base = {'case':case,'run_id':run,'stage_source':str(stagepath),'manual_relevance':labels.get(case,{}),'threshold_runs':[]}
        output['cases'].append(base)
        if run not in wires or 'retrieval' not in completed:
            base['status']='NOT_REPLAYABLE_NO_COMPLETE_RERANK_RECEIPT';continue
        wirepath, wire = wires[run]; rawpath = evidence/'raw'/(wirepath.stem+'.body')
        if not rawpath.exists(): base['status']='NOT_REPLAYABLE_NO_RAW_RESPONSE';continue
        raw=load(rawpath)['results']
        candidates=[SearchResult(**x) for x in completed['retrieval']['search_results'] if x is not None and rr.enriched_passage(SearchResult(**x)).strip()]
        assert [rr.enriched_passage(c) for c in candidates] == wire['body']['documents'], case
        validate_raw_results(raw, len(candidates))
        configpath=evidence/'configuration'/stagepath.name
        config=load(configpath)['pipeline_state']
        capturepath=evidence/'captures'/stagepath.name
        capture=load(capturepath) if capturepath.exists() else {}
        targets=tuple(SearchTarget(**t) for t in config['targets'])
        fields_state={f.name for f in fields(PipelineState)}
        stateconfig={k:v for k,v in config.items() if k in fields_state and k!='targets'}
        mismatches=[]
        for c in candidates:
            current=rows.get(c.id)
            differences = snapshot_differences(c, current)
            if differences: mismatches.append({'id':c.id,'reason':'SOURCE_IDENTITY_OR_CONTENT_CHANGED','fields':differences})
        base.update(status='REPLAYED',wire_source=str(wirepath),response_source=str(rawpath),input_documents_sha256=textsha(json.dumps(wire['body']['documents'],ensure_ascii=False)),historical_source_overlap={'compared':len(candidates),'mismatches':mismatches},candidates=[])
        rawmap={r['index']:r['relevance_score'] for r in raw}
        for i,c in enumerate(candidates):
            base['candidates'].append({'id':c.id,'chunk_type':c.chunk_type,'knowledge_id':c.knowledge_id,'input_index':i,'retrieval_score':c.score,'raw_score':rawmap.get(i),'content_chars':len(c.content),'passage_chars':len(rr.enriched_passage(c)),'content_sha256':textsha(c.content),'focus_role':focusmap.get(case,{}).get(c.id),'manual_relevance':labels.get(case,{}).get(c.id,'UNKNOWN')})
        class SavedModel:
            async def rerank(self,query,documents,context):
                assert query==wire['body']['query'] and documents==wire['body']['documents']
                return [NS(index=r['index'],score=r['relevance_score']) for r in raw]
        class SavedRegistry:
            async def rerank(self,*args): return SavedModel()
        for threshold in args.thresholds:
            state=PipelineState(**stateconfig)
            state.query=result['question'];state.rewrite_query=wire['body']['query'];state.targets=targets
            state.search_results=deepcopy(candidates);state.rerank_threshold=threshold
            state.history=deepcopy(completed['retrieval'].get('history',[]))
            context=NS(scope=NS(knowledge_base_ids=tuple(t.knowledge_base_id for t in targets), document_ids=tuple(result.get('document_scope',[]))),child=lambda _:NS())
            accepted=[]; actual_mmr=rr.mmr
            def observed_mmr(c,passages,k,segmenter=None):
                accepted.extend(deepcopy(c));return actual_mmr(c,passages,k,segmenter)
            rr.mmr=observed_mmr
            runout={'threshold':threshold,'status':'PASS','through_threshold_ids':[],'through_mmr_ids':[]}
            try:
                await rr.rerank(state,context,SavedRegistry(),segmenter)
            except SearchNothing:
                runout['status']='SEARCH_NOTHING'
            finally: rr.mmr=actual_mmr
            runout['through_threshold_ids']=[c.id for c in accepted]
            runout['through_mmr_ids']=[c.id for c in state.rerank_results]
            runout['post_mmr']=[{'id':c.id,'score':c.score} for c in state.rerank_results]
            if threshold==0.3:
                saved=completed.get('rerank',{}).get('rerank_results',[])
                runout['baseline_rerank_exact']=asdict_list(state.rerank_results)==saved
            if runout['status']=='SEARCH_NOTHING':
                runout.update(merge_status='NOT_RUN_PIPELINE_STOPS',through_merge_ids=[],through_top_k_ids=[],rendered_contexts='')
            elif not snapshot or mismatches:
                runout['merge_status']='NOT_REPLAYABLE_MISSING_OR_CHANGED_SOURCE_SNAPSHOT'
            else:
                repo=SavedRepository(rows)
                try:
                    await merge(state,context,repo,segmenter)
                    runout['through_merge_ids']=[c.id for c in state.merge_results]
                    runout['merge_rows']=asdict_list(state.merge_results)
                    filter_top_k(state)
                    runout['through_top_k_ids']=[c.id for c in state.merge_results]
                    runout['top_k_rows']=asdict_list(state.merge_results)
                    final=await into_chat_message(state,context)
                    runout.update(merge_status='PASS',rendered_contexts=final.rendered_contexts,rendered_contexts_sha256=textsha(final.rendered_contexts),snapshot_rows_used=sorted(repo.requested))
                    if threshold==0.3:
                        runout['baseline_merge_exact']=asdict_list(state.merge_results)==completed.get('context',{}).get('merge_results',[])
                        runout['baseline_rendered_context_exact']=final.rendered_contexts==capture.get('context',{}).get('rendered_contexts')
                except PermissionError as error:
                    runout.update(merge_status='NOT_REPLAYABLE_MISSING_SOURCE_ROWS',missing_detail=str(error))
            focusids=focusmap.get(case,{})
            runout['targets']=[{'id':i,'kind':kind,'threshold':i in runout['through_threshold_ids'],'mmr':i in runout['through_mmr_ids'],
                'merge':any(i==r['id'] or i in r['sub_chunk_ids'] for r in runout.get('merge_rows',[])),
                'top_k':any(i==r['id'] or i in r['sub_chunk_ids'] for r in runout.get('top_k_rows',[])),
                'rendered_exact_content':bool(next((c.content in runout.get('rendered_contexts','') for c in candidates if c.id==i),False))} for i,kind in focusids.items()]
            runout['candidate_transitions'] = []
            for c in candidates:
                flags = {'threshold':c.id in runout['through_threshold_ids'], 'mmr':c.id in runout['through_mmr_ids'],
                         'merge':any(c.id == r['id'] or c.id in r['sub_chunk_ids'] for r in runout.get('merge_rows',[])),
                         'top_k':any(c.id == r['id'] or c.id in r['sub_chunk_ids'] for r in runout.get('top_k_rows',[])),
                         'rendered_exact_content':c.content in runout.get('rendered_contexts','')}
                first_loss=next((name for name,flag in flags.items() if not flag),'NONE')
                runout['candidate_transitions'].append({'id':c.id,**flags,'first_loss':first_loss})
            base['threshold_runs'].append(runout)
        print(case,[(x['threshold'],len(x['through_mmr_ids']),x.get('merge_status')) for x in base['threshold_runs']])
    for p in [Path(__file__), REPO/'backend/weknora_port/retrieval/rerank.py',REPO/'backend/weknora_port/retrieval/merge.py',REPO/'backend/weknora_port/retrieval/filter_top_k.py',REPO/'backend/weknora_port/retrieval/context.py',REPO/'backend/weknora_port/retrieval/pipeline.py',REPO/'backend/weknora_port/retrieval/types.py']:
        provenance[str(p)]=sha(p)
    output['input_hashes']=provenance
    baseline_checks=[(c['case'],k,v) for c in output['cases'] for r in c['threshold_runs'] if r['threshold']==.3 for k,v in r.items() if k.startswith('baseline_')]
    output['baseline_consistency']={'checks':len(baseline_checks),'all_match':all(v for _,_,v in baseline_checks),'failures':[(c,k) for c,k,v in baseline_checks if not v]}
    output['status'], exit_code, output['incomplete_runs'] = replay_verdict(output['cases'], output['baseline_consistency']['all_match'])
    output['exit_code'] = exit_code
    output['limitations']=['Current source snapshot is not historical; overlapping historical candidate content/source_version/knowledge_base_id/knowledge_id/chunk_type compared strictly. Baseline output equality additionally verifies replay at captured threshold.', 'No new answer generation or answer quality verdict. Unlabeled candidate relevance remains UNKNOWN.', 'Only cases with complete saved rerank wire and raw response replayed. No rerank receipts synthesized.']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('RESULT',args.output,'SHA256',sha(args.output))
    print('BASELINE',output['baseline_consistency'])
    if exit_code: raise SystemExit(exit_code)

def asdict_list(rows):return [asdict(c) for c in rows]
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path(r'E:\WK\run\rag-m3-20261017'))
    p.add_argument('--focus',type=Path,default=Path(r'E:\WK\run\rag-m31-20261018\omni-evidence-flow.json'))
    p.add_argument('--snapshot',type=Path)
    p.add_argument('--labels',type=Path)
    p.add_argument('--thresholds',type=float,nargs='+',default=[.3,.25,.2,.15,0])
    p.add_argument('--output',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
