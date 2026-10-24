"""Freeze an independent, offline R2 input contract from task-input identities.

This module has no application, provider, database or network dependencies.  It
never chooses a document from answers, qrels, supporting facts, evidence pages,
retrieved chunks, or previous correctness labels.  Private files are supplied at
runtime; the repository contains only the compiler and synthetic tests.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = 'ECS55_INPUT_CONTRACT_V2'
TASK_FIELDS = {
    'longbench-zh': ('paired_source_doc_id', 'SPECIFIED_DOCUMENT'),
    'mmlongbench-doc': ('doc_id', 'SPECIFIED_DOCUMENT'),
    'hotpotqa': (None, 'FULL_AUTHORIZED_KB'),
}
MAPPING_BASES = {
    'EXACT_SOURCE_ID_AND_ORIGINAL_UPLOAD_SHA',
    'EXACT_DATASET_DOC_ID_AND_ORIGINAL_PDF_SHA',
}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def file_proof(path: Path) -> dict:
    raw = path.read_bytes()
    return {'path': str(path.resolve()), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def verify_file(proof: dict) -> None:
    actual = file_proof(Path(proof['path']))
    if (actual['bytes'], actual['sha256']) != (proof['bytes'], proof['sha256']):
        raise ValueError('SOURCE_FILE_HASH_MISMATCH: ' + proof['path'])


def project_task_input(dataset: str, raw: dict) -> dict:
    """Whitelist input fields from the ORIGINAL dataset row, ignoring all Gold.

    MMLongBench's selection wrapper stores the original row under original_row.
    Hotpot context/supporting_facts are deliberately never examined here.
    """
    if dataset not in TASK_FIELDS:
        raise ValueError('UNSUPPORTED_DATASET: ' + dataset)
    source_field, task_kind = TASK_FIELDS[dataset]
    if dataset == 'mmlongbench-doc':
        row, qid = raw['original_row'], raw['qid']
    elif dataset == 'hotpotqa':
        row, qid = raw, raw['id']
    else:
        row, qid = raw, raw['qid']
    question = row['question']
    source_id = row[source_field] if source_field else None
    if not isinstance(question, str) or not question:
        raise ValueError('TASK_QUESTION_REQUIRED')
    if source_field and (not isinstance(source_id, str) or not source_id):
        raise ValueError('TASK_INPUT_DOCUMENT_ID_REQUIRED')
    return {'dataset': dataset, 'qid': qid, 'question': question,
            'task_kind': task_kind, 'input_source_id_field': source_field,
            'input_source_id': source_id}


def project_jsonl_inputs(dataset: str, path: Path) -> list[dict]:
    proof = file_proof(path)
    records = []
    for number, line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        if not line.strip():
            continue
        projection = project_task_input(dataset, json.loads(line))
        records.append({**projection, 'input_proof': {
            'kind': 'DATASET_TASK_INPUT', 'source_file': proof, 'line': number,
            'record_sha256_including_line_ending': hashlib.sha256(line).hexdigest(),
            'input_projection_sha256': canonical_sha256(projection),
            'selected_fields': ['qid', 'question'] +
                ([projection['input_source_id_field']] if projection['input_source_id_field'] else []),
        }})
    return records


def longbench_source_identity(task: dict, raw_sample: dict, corpus_row: dict) -> dict:
    """Prove the paired source is the original sample context, without Gold."""
    sample_id = raw_sample['dataset'] + ':' + raw_sample['_id']
    if (task['qid'] != sample_id or task['input_source_id'] != sample_id
            or task['question'] != raw_sample['input']
            or corpus_row['doc_id'] != sample_id
            or corpus_row['text'] != raw_sample['context']):
        raise ValueError('ORIGINAL_TASK_CONTEXT_IDENTITY_MISMATCH')
    original_bytes = ('# ' + corpus_row['title'] + '\n\n' + corpus_row['text']).encode('utf-8')
    return {'input_source_id': sample_id,
            'original_task_context_sha256': text_sha256(raw_sample['context']),
            'original_source_sha256': hashlib.sha256(original_bytes).hexdigest(),
            'original_source_bytes': len(original_bytes),
            'projection_rule': "UTF8('# '+title+'\\n\\n'+text); no trimming or Gold"}


def verify_task_record(task: dict) -> None:
    """Re-read the pinned source row so a supplied projection cannot spoof Gold."""
    allowed = {'dataset', 'qid', 'question', 'task_kind', 'input_source_id_field',
               'input_source_id', 'input_proof'}
    if set(task) != allowed or task['input_proof']['kind'] != 'DATASET_TASK_INPUT':
        raise ValueError('TASK_INPUT_PROJECTION_FIELDS_INVALID')
    proof = task['input_proof']
    verify_file(proof['source_file'])
    lines = Path(proof['source_file']['path']).read_bytes().splitlines(keepends=True)
    line = lines[proof['line'] - 1]
    if hashlib.sha256(line).hexdigest() != proof['record_sha256_including_line_ending']:
        raise ValueError('TASK_INPUT_RECORD_HASH_MISMATCH')
    actual = project_task_input(task['dataset'], json.loads(line))
    supplied = {key: task[key] for key in allowed if key != 'input_proof'}
    if actual != supplied or canonical_sha256(actual) != proof['input_projection_sha256']:
        raise ValueError('TASK_INPUT_PROJECTION_MISMATCH')


def _unique(rows: list[dict], fields: tuple[str, ...]) -> dict:
    result = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        if key in result:
            raise ValueError('DUPLICATE_IDENTITY: ' + repr(key))
        result[key] = row
    return result


def validate_binding(binding: dict) -> None:
    if binding.get('mapping_basis') not in MAPPING_BASES:
        raise ValueError('DOCUMENT_MAPPING_NOT_INPUT_IDENTITY')
    if binding.get('source_hash_matches_original_upload') is not True:
        raise ValueError('ORIGINAL_UPLOAD_HASH_NOT_PROVEN')
    if binding.get('current_enabled') is not True:
        raise ValueError('DOCUMENT_NOT_CURRENT_ENABLED')
    if any(not binding.get(field) for field in
           ('document_id', 'knowledge_base_id', 'source_version', 'original_source_sha256')):
        raise ValueError('DOCUMENT_IDENTITY_INCOMPLETE')
    if len(binding['original_source_sha256']) != 64:
        raise ValueError('SOURCE_SHA256_INVALID')
    if not binding.get('identity_proofs'):
        raise ValueError('DOCUMENT_IDENTITY_PROOF_REQUIRED')


def freeze_contract(bundle: dict, *, verify_sources: bool = True) -> dict:
    cases = bundle['original_inputs']
    expected = bundle.get('expected_count', 55)
    if len(cases) != expected or sorted(c['ordinal'] for c in cases) != list(range(expected)):
        raise ValueError('ORIGINAL_DENOMINATOR_OR_ORDINAL_CHANGED')
    _unique(cases, ('case_id',))
    task_by_key = _unique(bundle['task_inputs'], ('dataset', 'qid'))
    bindings: dict[tuple[str, str], list[dict]] = {}
    for row in bundle['document_bindings']:
        bindings.setdefault((row['dataset'], row['input_source_id']), []).append(row)
    if verify_sources:
        for proof in bundle.get('source_files', []):
            verify_file(proof)
        for task in task_by_key.values():
            verify_task_record(task)
    rows = []
    for original in sorted(cases, key=lambda c: c['ordinal']):
        if text_sha256(original['question']) != original['q0_sha256']:
            raise ValueError('ORIGINAL_Q0_HASH_MISMATCH')
        task = task_by_key.get((original['dataset'], original['qid']))
        new_scope = deepcopy(original['scope'])
        status, reason, binding = 'BLOCKED_MISSING_TASK_INPUT', 'No independent task-input row.', None
        task_change = False
        if task:
            if task['question'] != original['question']:
                raise ValueError('TASK_QUESTION_IDENTITY_MISMATCH: ' + original['case_id'])
            field, kind = TASK_FIELDS[original['dataset']]
            if task['input_source_id_field'] != field or task['task_kind'] != kind:
                raise ValueError('TASK_DEFINITION_MISMATCH')
            if kind == 'FULL_AUTHORIZED_KB':
                if original['scope']['document_ids']:
                    raise ValueError('HOTPOT_ORIGINAL_SCOPE_NOT_FULL_KB')
                status, reason = 'FROZEN', 'Preserve full authorized KB; no support-document filter.'
            else:
                matches = bindings.get((original['dataset'], task['input_source_id']), [])
                # Identical source bytes can exist in multiple dataset KBs.
                # Preserve the original authorized KB whenever it has a match;
                # do not cross KBs merely because another copy exists.
                in_original_kb = [item for item in matches if item['knowledge_base_id']
                                  in original['scope']['knowledge_base_ids']]
                if in_original_kb:
                    matches = in_original_kb
                if len(matches) == 1:
                    binding = matches[0]
                    try:
                        validate_binding(binding)
                    except ValueError as error:
                        status, reason = 'BLOCKED_UNPROVEN_DOCUMENT_IDENTITY', str(error)
                    else:
                        new_scope['knowledge_base_ids'] = [binding['knowledge_base_id']]
                        new_scope['document_ids'] = [binding['document_id']]
                        status = 'FROZEN'
                        reason = 'Restore specified-document association from dataset task input and original source hash.'
                        task_change = new_scope != original['scope']
                elif matches:
                    status, reason = 'BLOCKED_AMBIGUOUS_DOCUMENT_IDENTITY', 'More than one eligible document identity.'
                else:
                    status, reason = 'BLOCKED_MISSING_DOCUMENT_IDENTITY', 'Task input is proven; source-to-current-document identity is missing.'
        changed = new_scope != original['scope']
        rows.append({
            'ordinal': original['ordinal'], 'case_id': original['case_id'],
            'dataset': original['dataset'], 'qid': original['qid'],
            'old_question': original['question'], 'new_question': original['question'],
            'q0_sha256': original['q0_sha256'], 'question_changed': False,
            'old_scope': deepcopy(original['scope']), 'new_scope': new_scope,
            'scope_changed': changed, 'status': status, 'change_reason': reason,
            'task_definition_changed_from_A_whole_kb': task_change,
            'original_input_proof': deepcopy(original['original_input_proof']),
            'task_input': deepcopy(task), 'source_identity': deepcopy(binding),
            'gold_sha256_canonical': original['gold_sha256_canonical'],
            'historical_label': original.get('historical_label'),
            'review_notes': deepcopy(original.get('review_notes', [])),
            'generation_input_forbidden_fields': ['answers', 'Gold', 'qrels', 'supporting_facts', 'evidence_pages'],
        })
    return {
        'schema': SCHEMA, 'status': 'FROZEN_OFFLINE' if all(r['status'] == 'FROZEN' for r in rows) else 'PARTIALLY_FROZEN_BLOCKED',
        'scope': 'INDEPENDENT_R2_VERSION; original A and historical results preserved',
        'provider_calls': 0, 'database_calls': 0, 'ssh_calls': 0,
        'historical': deepcopy(bundle['historical']), 'source_files': deepcopy(bundle.get('source_files', [])),
        'bundle_sha256_canonical': canonical_sha256(bundle),
        'counts': {'denominator': expected, 'question_changes': 0,
                   'scope_changes': sum(r['scope_changed'] for r in rows),
                   'by_status': dict(Counter(r['status'] for r in rows))},
        'document_identity_selection_rule': 'Exact input source ID plus original source SHA; prefer a unique current enabled copy in original authorized KB. Only when absent may a unique proven authorized source KB correct the independent R2 scope. Multiple remaining candidates block.',
        'pre_execution_requirement': 'Fresh read-only owner/KB/document/version/hash/cloud-consent checks before any execution; this offline freeze grants no sending authority.',
        'cases': rows,
    }


def scoring_protocol() -> dict:
    """Prospective rules only; this function does not rescore historical answers."""
    return {
        'schema': 'ECS55_R2_SCORING_PROTOCOL_V1', 'timing': 'PREFROZEN_BEFORE_R2_GENERATION',
        'historical_labels': 'IMMUTABLE; A Cheap 40/55 and reviewed union 41/55 stay separate from R2.',
        'denominator': 'All 55 original identities, including missing/blocked results; report unresolved separately.',
        'dimensions': {
            'core_correctness': 'CORRECT/PARTIAL/WRONG/NO_RESULT; compare required answer propositions, not keyword overlap. For a bare category/list query with no exhaustive wording, correct relevant instances without material contradictions can pass core; no numeric Gold-list coverage threshold is imposed.',
            'strict_historical_gold_match': 'MATCH/PARTIAL/MISMATCH/SOURCE_GOLD_CONFLICT/NO_RESULT; preserve exact historical Gold and report this separately from source-supported core factual correctness. A proven source/Gold conflict is recorded as errata with source revision/hash/page evidence; never silently edit Gold or past labels.',
            'enumeration_completeness': 'COMPLETE/PARTIAL/NOT_APPLICABLE; a bare category query is not automatically an exhaustive-list request. Enumerations asking all/every/complete require every frozen item. Count coverage of a frozen Gold enumeration separately even when the bare-query core criterion passes.',
            'contradictions': 'Explicit unambiguous withdrawal or correction of an earlier statement is judged by the corrected final position; merely appending a conflicting alternative leaves an unresolved contradiction. A material unresolved contradiction makes core WRONG; minor extra unsupported facts are tracked separately.',
            'clarification': 'REASONABLE_CLARIFICATION/UNNECESSARY_CLARIFICATION/NOT_APPLICABLE; reasonable clarification is valid interaction behavior but is not a factual-answer rescue and does not increment core-correct numerator.',
            'citations': 'Track existence, resolvability, scope/version validity and claim support independently from core.',
            'source_fidelity_and_external_truth': 'Keep fidelity to frozen sources separate from globally verified factual truth.',
        },
        'ordinal4_wording_review': {
            'question_kind': 'Bare category question about US holidays; wording does not explicitly demand all 19 items.',
            'historical_decision': 'Preserve historical PARTIAL/CORRECT labels and 19-item completeness review verbatim.',
            'prospective_rule': 'For R2, correct relevant US holidays without material contradictions can pass core; the 19-item complete-enumeration measure is separate. There is no invented numeric threshold for bare-query core. Historical scores stay unchanged; R2 uses this prospective rule explicitly.',
            'status': 'WORDING_REVIEW_COMPLETE; prospective core and completeness rules frozen before R2 outputs.',
        },
        'ordinal18_source_gold_conflict': 'Record independently verified same-revision source/Gold conflicts as SOURCE_GOLD_CONFLICT. Evaluate the actual endpoint separately from strict historical Gold match; a separately printed page/footer number must never be appended to a URL. Preserve original Gold, old labels and original actual Context limitations.',
        'ordinal52_full_document_counting': 'Separate capability: requires complete original-document page/image inventory plus provenance, not a count over retrieved local Context. Missing inventory is BLOCKED/INSUFFICIENT_EVIDENCE, never assumed zero.',
        'comparison_policy': 'Report each prompt/model/input/source version separately; matched union is not automatic-router accuracy.',
    }


def inference_inputs(contract: dict) -> dict:
    """Build the executable input projection; no Gold or labels enter it."""
    if contract['status'] != 'FROZEN_OFFLINE':
        raise ValueError('INPUT_CONTRACT_NOT_FULLY_FROZEN')
    cases = []
    for row in contract['cases']:
        cases.append({'ordinal': row['ordinal'], 'case_id': row['case_id'],
                      'dataset': row['dataset'], 'qid': row['qid'],
                      'question': row['new_question'], 'q0_sha256': row['q0_sha256'],
                      **deepcopy(row['new_scope'])})
    return {'schema': 'ECS55_R2_INFERENCE_INPUTS_V1', 'input_contract_schema': SCHEMA,
            'input_contract_sha256_canonical': canonical_sha256(contract),
            'gold_included': False, 'historical_labels_included': False,
            'count': len(cases), 'cases': cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding='utf-8-sig'))
    result = freeze_contract(bundle)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in [('manifest.private.json', result), ('scoring-protocol.private.json', scoring_protocol())]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if result['status'] == 'FROZEN_OFFLINE':
        (args.out / 'inference-inputs.private.json').write_text(
            json.dumps(inference_inputs(result), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# ECS55_INPUT_CONTRACT_V2 — private offline diff', '',
             'Original A, q0, Gold, historical 40/55 and 41/55 remain preserved.', '',
             '| Ordinal | Dataset | Status | q changed | Scope changed | Change |',
             '|---:|---|---|---|---|---|']
    for row in result['cases']:
        lines.append(f"| {row['ordinal']} | {row['dataset']} | {row['status']} | false | {str(row['scope_changed']).lower()} | {row['change_reason']} |")
    (args.out / 'diff.private.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'counts': result['counts'], 'provider_calls': 0}))
    return 0 if result['status'] == 'FROZEN_OFFLINE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
