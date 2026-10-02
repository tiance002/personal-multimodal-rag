"""Offline identity/owner overlay utilities; never imported by production RAG."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

REFERENCE_SHA = '7b7468dcdde96fe17bfe35d4acdfd070b7ba299082af656d257c49716f856291'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def initialize(root: Path):
    frozen = root / 'var/reports/real-material-quality-baseline'
    reference = frozen / 'reference-set.json'
    if digest(reference) != REFERENCE_SHA:
        raise ValueError('Frozen Reference mismatch')
    output = root / 'var/reports/local-v1.1-hardening'
    protected = [reference, frozen / 'quality-scores.json',
                 root / 'var/local-first/real-material-eval.jsonl', root / 'var/local-first/materials.json',
                 root / 'var/reports/real-materials-20260930/results.json',
                 root / 'var/reports/real-materials-20260930/manifest.json',
                 root / 'docs/reviews/real-material-quality-baseline-20260930.md']
    save(output / 'immutable-inputs.json', {str(p.relative_to(root)): digest(p) for p in protected})
    judge = json.loads((frozen / 'quality-scores.json').read_text(encoding='utf-8'))
    by_id = {r['case_id']: r for r in judge['cases']}
    reviews = {
        'real-001': ({}, '基本正确；扩展内容属于答案范围且受对应资料支持，不因超出标题而扣分。'),
        'real-004': ({'primary_failure_type': 'SYSTEM_OVER_REFUSAL', 'system_over_refusal': 'CONFIRMED', 'validator_over_rejection': 'NOT_PROVEN'}, '原资料有答案，系统最终拒答；拒绝的 candidate 未保存，不能证明 Validator 误判。'),
        'real-008': ({'secondary_failure_types': ['SELF_CONTRADICTION', 'LOCAL_MODEL_FACTUAL_ERROR']}, 'Context 有答案；先否认再引用形成明显矛盾。模型明确指出无关片段不单独计作幻觉。'),
        'real-009': ({'primary_failure_type': 'SYSTEM_OVER_REFUSAL', 'system_over_refusal': 'CONFIRMED', 'validator_over_rejection': 'NOT_PROVEN'}, '原资料有答案，系统拒答；缺少原 candidate，Validator 责任仍未证明。'),
        'real-011': ({'primary_failure_type': 'GENERATION_TRUNCATION', 'secondary_failure_types': ['MULTI_INTENT_COVERAGE_FAILURE', 'LOCAL_MODEL_OMISSION', 'CITATION_SELECTION_FAILURE']}, '多个意图只覆盖部分；同时存在截断和引用错配。原记录达到 512 输出上限，截断作为可观测主链故障，非唯一原因。'),
        'real-016': ({'retrieval_sufficiency': 'SUFFICIENT', 'correctness': 2, 'completeness': 1, 'faithfulness': 2, 'primary_failure_type': 'LOCAL_MODEL_OMISSION'}, '证据充分，核心逻辑正确，无明显幻觉；关键步骤遗漏，撤销 Retrieval Failure 归因。'),
        'real-017': ({'corpus_answerable': 'YES', 'retrieval_sufficiency': 'INSUFFICIENT', 'primary_failure_type': 'RETRIEVAL_FAILURE', 'secondary_failure_types': ['OVER_REFUSAL']}, '资料可答不等于 selected context 可答；必要目标字段未进入 Context，不能归因 Cloud 或 Validator。'),
        'real-023': ({'retrieval_sufficiency': 'SUFFICIENT', 'secondary_failure_types': ['CITATION_IDENTITY_INCONSISTENCY', 'CITATION_RELEVANCE_FAILURE', 'LOCAL_MODEL_REASONING_FAILURE', 'LOCAL_MODEL_OMISSION']}, 'Context 充分；存在推理与遗漏、无关回答及引用，实质相同证据表现为不同引用编号。'),
    }
    rows = []
    for case_id, (override, reason) in reviews.items():
        original = by_id[case_id]
        rows.append({'case_id': case_id, 'judge_original': original, 'human_override': override,
                     'override_reason': reason, 'human_review': {'status': 'REVIEWED', **override},
                     'final_attribution': {'source': 'HUMAN_OVERRIDE', **override},
                     'applies_to': 'V1_BASELINE_ANSWER_ONLY'})
    save(output / 'human-review-overlay.json', {'schema_version': 'human-review-overlay-v1',
         'human_review': 'PARTIAL', 'human_review_cases': '8 / 32',
         'reference_set_sha256': REFERENCE_SHA, 'baseline_full_quality_gate': '20 / 32 = 62.5%',
         'v1_1_answer_review_status': 'NOT_REVIEWED', 'cases': rows})
    print(json.dumps({'status': 'PASS', 'immutable_files': len(protected), 'human_review_cases': 8}))


def verify(root: Path):
    ledger = json.loads((root / 'var/reports/local-v1.1-hardening/immutable-inputs.json').read_text(encoding='utf-8'))
    mismatches = [p for p, sha in ledger.items() if digest(root / p) != sha]
    if mismatches:
        raise ValueError('Immutable input changed: ' + ','.join(mismatches))
    print(json.dumps({'status': 'PASS', 'unchanged_files': len(ledger), 'reference_sha256': REFERENCE_SHA}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['initialize', 'verify'])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    (initialize if args.action == 'initialize' else verify)(args.root)
