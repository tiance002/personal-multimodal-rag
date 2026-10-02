"""One deterministic finalizer replay of preserved outputs; zero model/recall calls.

Does not overwrite generation-run records or invent final end-to-end latency.
No Reference answer points are read or injected into the application.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.adapters.answer_audit import LocalAnswerAudit
from backend.app.application.answer_hardening import VERSION, MARKER
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.models import ChunkRecord


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root
    base = root / 'var/reports/local-v1.1-hardening'
    output = base / 'deterministic-replay'
    output.mkdir(exist_ok=False)
    inputs = base / 'regression/results.json'
    data = json.loads(inputs.read_text(encoding='utf-8'))
    cases = {r['case_id']: r for line in (root/'var/local-first/real-material-eval.jsonl').read_text(encoding='utf-8').splitlines() if (r := json.loads(line))}
    corpus = json.loads((root/'var/local-first/active-corpus-postflight.json').read_text(encoding='utf-8'))
    chunks = {c['id']: ChunkRecord(c['id'], corpus['kb_id'], d['document']['id'], c['version_id'], c['content'], c['locator']) for d in corpus['documents'] for c in d['chunks']}
    originals = {}
    for path in (root/'var/local-first/storage/answer-audit').glob('*.json'):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['run_id'] in originals:
            raise ValueError('ambiguous original rejected candidate')
        originals[record['run_id']] = record
    service = EvidenceService(answer_audit=LocalAnswerAudit(output/'local-audit'))
    rows = []
    for original in data['per_case']:
        row = copy.deepcopy(original)
        result = row['result']
        original_metrics = copy.deepcopy(result['trace']['metrics'])
        run_id = result['run_id']
        selected = [chunks[c] for c in original_metrics['selected_context_chunk_ids']]
        citations = CitationService(InMemoryCitationStore())
        for index, chunk in enumerate(selected, 1):
            citations.freeze(run_id, chunk, f'E{index}')
        snapshots = tuple(citations.snapshots.values())
        plan = service.plan(cases[row['case_id']]['question'])
        audit = originals.get(run_id)
        candidate = audit['candidate_local_audit_payload']['text'] if audit else result['answer']
        started = time.perf_counter()
        with collect_metrics(run_id) as metrics:
            if result['trace'].get('degradation_code') == 'PRIVACY_CONFIG_EVIDENCE_ONLY':
                answer, error, rejection, fallback = candidate, None, None, None
                service.hardening.observe_evidence_answer(answer, snapshots, selected, plan)
            else:
                finalized = service.finalize_answer(run_id, candidate, snapshots, selected, plan)
                answer, error, rejection, fallback = finalized.answer, finalized.error, finalized.rejection, finalized.fallback
            observed = copy.deepcopy(metrics.hardening)
        labels = tuple(dict.fromkeys(MARKER.findall(answer)))
        result['answer'], result['error_code'], result['citations'] = answer, error, list(labels)
        result['trace']['reason_codes'] = [x for x in (rejection, fallback) if x]
        result['trace']['metrics'] = original_metrics | {'answer_hardening': observed, 'error': error, 'citations': list(labels)}
        row['citation_details'] = [citations.resolve(run_id, label).__dict__ for label in labels]
        row['replay'] = {'type': 'DETERMINISTIC_FINALIZER_REPLAY', 'version': VERSION,
            'new_model_calls': 0, 'new_retrieval_calls': 0,
            'generation_run_id': run_id, 'candidate_from': 'LOCAL_REJECTED_AUDIT' if audit else 'PRESERVED_ACCEPTED_OUTPUT',
            'candidate_hash': hashlib.sha256(candidate.encode()).hexdigest(),
            'postprocess_latency_ms': (time.perf_counter()-started)*1000,
            'original_end_to_end_latency_ms': original_metrics['total_latency_ms'],
            'final_live_end_to_end_latency': 'NOT_RUN'}
        (output/(row['case_id']+'.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        rows.append(row)
    report = {'schema_version': VERSION, 'type': 'DETERMINISTIC_FINALIZER_REPLAY',
        'source_results_sha256': hashlib.sha256(inputs.read_bytes()).hexdigest(),
        'new_model_calls': 0, 'new_retrieval_calls': 0, 'new_cloud_calls': 0,
        'final_live_end_to_end_regression': 'NOT_RUN', 'per_case': rows}
    (output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status':'PASS','cases':len(rows),'new_model_calls':0,'new_retrieval_calls':0,
        'changed_outputs':[r['case_id'] for r, o in zip(rows,data['per_case']) if r['result']['answer'] != o['result']['answer'] or r['result']['error_code'] != o['result']['error_code']]}))


if __name__ == '__main__':
    main()
