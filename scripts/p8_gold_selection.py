"""Freeze a small P8 selection from original package bytes; no models or DB.

Selection contains only upload originals and questions. Gold remains in separate
evaluation files. Package source counts are assets, never question counts.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_center.weknora_frozen_gold import load_frozen_gold
from eval_center.weknora_source_packages import checked_bytes

EXPECTED_UNITS = {'scifact': 200, 'miracl-zh': 400, 'longbench-zh': 48,
                  'hotpotqa': 73, 'mmlongbench-doc': 3, 'omnidocbench': 24}
FORBIDDEN = {'answer', 'answers', 'qrels', 'question', 'qid', 'supporting_facts',
             'gold', 'gold_answer', 'gold_answers', 'gold_evidence',
             'evidence_pages', 'evidence_sources', 'expected_chunk_ids', 'answer_points'}
OMNI_QUESTION = '请依据图片说明该页主要内容，并引用图片中的文字证据。'
RULES = {
    'version': 'p8-predeclared-selection-v1',
    'scifact': 'Frozen QIDs sorted lexically; first and last with all positive qrels in package; each adds first two lexical nonpositive source IDs.',
    'miracl-zh': 'Same rule as SciFact, within frozen QIDs and fixed package passage pool.',
    'longbench-zh': 'First lexical frozen QID per task; complete paired context.',
    'hotpotqa': 'First lexical frozen QID with exactly two supporting documents and ten complete original contexts.',
    'mmlongbench-doc': 'First lexical frozen QID; complete original PDF.',
    'omnidocbench': 'First lexical frozen page ID; explicit diagnostic question, not official QA.',
    'selection_uses_model_outputs': False,
    'scope': 'Small source-selected smoke cohort, not full-corpus benchmark quality.',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def reject_gold_fields(value, path='$'):
    """Structural check, not a lexical scan for words occurring in source text."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN:
                raise ValueError('GOLD_FIELD_IN_PREPARED_SOURCE:' + path + '.' + key)
            reject_gold_fields(item, path + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_gold_fields(item, path + '[' + str(index) + ']')


def archive_ref(archive, ref):
    name = ref['path']
    relative = PurePosixPath(name)
    if (relative.is_absolute() or '\\' in name or ':' in name or
            any(part in ('.', '..') for part in relative.parts) or str(relative) != name):
        raise ValueError('PACKAGE_UNSAFE_RELATIVE_PATH')
    if archive.namelist().count(name) != 1:
        raise ValueError('PACKAGE_OBJECT_MISSING_OR_AMBIGUOUS:' + name)
    raw = archive.read(name)
    if len(raw) != ref['bytes'] or sha(raw) != ref['sha256']:
        raise ValueError('PACKAGE_OBJECT_IDENTITY_MISMATCH:' + name)
    return raw


def positive_ids(case):
    return {key for key, grade in case.qrels.items() if grade > 0}


def verify_hotpot_original(case, original, expected_context_ids):
    context = original['context']
    titles, sentences = context['title'], context['sentences']
    if len(titles) != len(sentences) or len(set(titles)) != len(titles):
        raise ValueError('HOTPOT_NATIVE_CONTEXT_ALIGNMENT_CHANGED')
    ids = ['hotpotqa-context:' + sha((title + '\0' + json.dumps(sents, ensure_ascii=False, separators=(',', ':'))).encode())
           for title, sents in zip(titles, sentences)]
    if (ids != expected_context_ids or original['question'] != case.question or
            (original['answer'],) != case.answers):
        raise ValueError('HOTPOT_NATIVE_CONTEXT_OR_QA_MAPPING_CHANGED')
    identity = dict(zip(titles, ids))
    facts = original['supporting_facts']
    if len(facts['title']) != len(facts['sent_id']):
        raise ValueError('HOTPOT_NATIVE_FACT_ALIGNMENT_CHANGED')
    expected_facts = {(identity[title], index) for title, index in zip(facts['title'], facts['sent_id'])}
    actual_facts = {(fact['document_id'], fact['sentence_index_zero_based'])
                    for fact in (case.evaluation_metadata or {})['supporting_facts']}
    if expected_facts != actual_facts or positive_ids(case) != {doc for doc, _ in expected_facts}:
        raise ValueError('HOTPOT_NATIVE_SUPPORTING_FACT_GOLD_CHANGED')


def choose_cases(name, cases, source_ids, hotpot_contexts=None):
    ordered = sorted(cases, key=lambda case: case.qid)
    if name in ('scifact', 'miracl-zh'):
        eligible = [c for c in ordered if positive_ids(c) and positive_ids(c) <= source_ids]
        if len(eligible) < 2:
            raise ValueError('TWO_MAPPABLE_FROZEN_CASES_REQUIRED:' + name)
        chosen = [eligible[0], eligible[-1]]
        sources = set()
        for case in chosen:
            positives = positive_ids(case)
            negatives = sorted(source_ids - positives)[:2]
            if len(negatives) != 2:
                raise ValueError('TWO_NONRELATED_DISTRACTORS_REQUIRED')
            sources.update(positives | set(negatives))
        return chosen, sources
    if name == 'longbench-zh':
        chosen = []
        for task in ('dureader', 'multifieldqa_zh'):
            chosen.append(next(c for c in ordered if c.qid.split(':')[0] == task))
        sources = {c.paired_source_doc_id for c in chosen}
    elif name == 'hotpotqa':
        chosen = [next(c for c in ordered if len(positive_ids(c)) == 2 and
                       len((hotpot_contexts or {}).get(c.qid, ())) == 10)]
        sources = set(hotpot_contexts[chosen[0].qid])
    elif name == 'mmlongbench-doc':
        chosen = ordered[:1]
        sources = positive_ids(chosen[0])
    else:
        raise ValueError('UNSUPPORTED_SELECTION_DATASET')
    if not sources or not sources <= source_ids:
        raise ValueError('SELECTED_SOURCE_OUTSIDE_PACKAGE:' + name)
    return chosen, sources


def verify_selection(selection):
    """Allowlist model-visible fields, so evaluation metadata cannot be copied."""
    if set(selection) != {'schema', 'selection_rules', 'datasets'}:
        raise ValueError('SELECTION_TOP_LEVEL_FIELDS_INVALID')
    for dataset in selection['datasets']:
        if set(dataset) != {'name', 'version', 'files', 'cases'}:
            raise ValueError('SELECTION_DATASET_FIELDS_INVALID')
        if not 1 <= len(dataset['cases']) <= 3:
            raise ValueError('SELECTION_CASE_COUNT_INVALID')
        for case in dataset['cases']:
            if set(case) != {'qid', 'question'}:
                raise ValueError('GOLD_FIELD_IN_SELECTION_CASE')
        for file in dataset['files']:
            if set(file) != {'source_id', 'path', 'file_name', 'media_type', 'sha256'}:
                raise ValueError('GOLD_FIELD_IN_SELECTION_FILE')


def build(package_root, manifest_path, manifest_sha256, output_dir):
    package_root, output_dir = Path(package_root), Path(output_dir)
    targets = [output_dir / name for name in ('selection.json', 'gold-audit.json', 'gold-selected.json')]
    if any(path.exists() for path in targets):
        raise ValueError('P8_OUTPUT_ALREADY_EXISTS')
    frozen = load_frozen_gold(manifest_path, manifest_sha256)
    freeze = json.loads(checked_bytes(manifest_path, manifest_sha256))
    index_raw = (package_root / 'six-package-manifest.json').read_bytes()
    index = json.loads(index_raw)
    if index.get('schema') != 'rag-v6-final-six-pack-index/v1':
        raise ValueError('PACKAGE_INDEX_SCHEMA_MISMATCH')
    dataset_map = {d.name: d for d in frozen.datasets}
    specs = {r['dataset']: r for r in freeze['datasets']}
    package_rows = index['packages']
    if len(package_rows) != 6 or {r['dataset'] for r in package_rows} != set(EXPECTED_UNITS):
        raise ValueError('SIX_PACKAGE_DATASETS_REQUIRED')
    hot_rows = json.loads(checked_bytes(specs['hotpotqa']['corpus']['path'], specs['hotpotqa']['corpus']['sha256']))
    hot_contexts = {}
    for row in hot_rows:
        hot_contexts.setdefault(row['source_qid'], []).append(row['source_document_id'])
    special = json.loads(checked_bytes(specs['hotpotqa']['source_manifest']['path'], specs['hotpotqa']['source_manifest']['sha256']))
    original_record = next(r for r in special['small_uploaded_files'] if r['name'] == 'hotpot-selected8.jsonl')
    original_path = Path(specs['hotpotqa']['source_manifest']['path']).parent.parent / original_record['name']
    original_hot = [json.loads(line) for line in checked_bytes(original_path, original_record['sha256'], original_record['bytes']).decode().splitlines() if line.strip()]
    original_hot = {row['id']: row for row in original_hot}
    for case in dataset_map['hotpotqa'].cases:
        verify_hotpot_original(case, original_hot[case.qid], hot_contexts[case.qid])

    audit = {'schema': 'p8-gold-selection-audit/v1', 'status': 'PASS',
             'manifest_sha256': manifest_sha256, 'package_index_sha256': sha(index_raw),
             'selection_rules': RULES, 'datasets': {}, 'source_mappings': [], 'case_mappings': [],
             'omni_page_mappings': [], 'exceptions': [], 'model_calls': 0, 'db_calls': 0,
             'coverage_unit': 'SOURCE_ASSETS_NOT_QUESTIONS', 'source_package_modified': False,
             'leakage_boundary': 'Exact original/corpus byte binding plus recursive prepared-JSON field checks; source words coinciding with answers are not leakage. Semantic annotation truth is not independently proved.',
             'prepared_artifacts_used_for_ingestion': False,
             'metric_contracts': {
                 'scifact': 'Official BEIR document qrels only; no SUPPORT/CONTRADICT/rationale Gold in this frozen adapter.',
                 'miracl-zh': 'Official qrels on fixed package passage subset; no QA Gold.',
                 'longbench-zh': 'Official answers; multifieldqa_zh Chinese token F1 / dureader Chinese ROUGE-L not run. Character F1 is project diagnostic only.',
                 'hotpotqa': 'Official answer and sentence supporting facts; full distractor context. Joint metric not run.',
                 'mmlongbench-doc': 'Official typed answers and original evidence pages; page base UNKNOWN, GPT normalization not run.',
                 'omnidocbench': 'Official parsing Gold only. Selected question is project diagnostic, not independently reviewed QA Gold.'}}
    packages = {}
    for row in package_rows:
        name = row['dataset']
        manifest_ref = row['manifest']
        manifest_raw = checked_bytes(package_root / manifest_ref['path'], manifest_ref['sha256'], manifest_ref['bytes'])
        package = json.loads(manifest_raw)
        if package.get('dataset') != name or package.get('Gold_qrels_answers_included') is not False:
            raise ValueError('PACKAGE_GOLD_BOUNDARY_MISMATCH:' + name)
        units = package['units']
        if len(units) != EXPECTED_UNITS[name]:
            raise ValueError('PACKAGE_SOURCE_COUNT_CHANGED:' + name)
        corpus = {d.doc_id: d for d in dataset_map[name].documents} if name in dataset_map else {}
        files = {d['doc_id']: d for d in dataset_map[name].files} if name in dataset_map else {}
        pages = {p['document_id']: p for p in frozen.omni_pages}
        archive_pin = row['archive']
        checked_bytes(package_root / archive_pin['path'], archive_pin['sha256'], archive_pin['bytes'])
        found = {}; normalized_count = 0
        with zipfile.ZipFile(package_root / archive_pin['path']) as archive:
            for unit in units:
                source = unit['source']; identity = source['source_document_id']
                record = {'dataset': name, 'unit_id': unit['unit_id'], 'source_id': identity,
                          'source_sha256': source['artifact']['sha256'], 'status': 'PASS'}
                try:
                    if identity in found:
                        raise ValueError('DUPLICATE_PACKAGE_SOURCE_ID')
                    raw = archive_ref(archive, source['artifact'])
                    if source['artifact']['path'] != 'objects/' + sha(raw):
                        raise ValueError('SOURCE_OBJECT_PATH_NOT_CONTENT_ADDRESS')
                    if Path(source['file_name']).name != source['file_name'] or any(c in source['file_name'] for c in ('/', '\\', ':')):
                        raise ValueError('SOURCE_FILENAME_INVALID')
                    if name in ('scifact', 'miracl-zh', 'longbench-zh'):
                        doc = corpus[identity]
                        title = doc.title.strip()
                        expected = (('# ' + title + '\n\n') if title else '') + doc.text
                        if raw != expected.encode('utf-8'):
                            raise ValueError('PACKAGE_SOURCE_DIFFERS_FROM_FULL_CORPUS')
                        if source['provenance']['original_corpus_sha256'] != specs[name]['corpus']['sha256']:
                            raise ValueError('PACKAGE_CORPUS_PROVENANCE_CHANGED')
                        record['binding'] = 'Exact existing Markdown source adapter bytes; no reconstruction used for upload.'
                    elif name == 'hotpotqa':
                        if raw != corpus[identity].text.encode('utf-8'):
                            raise ValueError('HOTPOT_ORIGINAL_SENTENCES_CHANGED')
                        record['binding'] = 'Exact complete original sentence-list text.'
                    elif name == 'mmlongbench-doc':
                        if sha(raw) != files[identity]['sha256']:
                            raise ValueError('MML_ORIGINAL_PDF_CHANGED')
                        record['binding'] = 'Full original PDF hash, all pages.'
                    else:
                        page = pages[identity]
                        if sha(raw) != page['image_sha256']:
                            raise ValueError('OMNI_ORIGINAL_PAGE_CHANGED')
                        record['binding'] = 'Exact frozen original image and separate page Gold.'
                    prepared = unit.get('prepared')
                    if prepared:
                        for field in ('normalized_json', 'chunks_json', 'assets_json'):
                            reject_gold_fields(json.loads(archive_ref(archive, prepared[field])))
                        normalized_count += 1
                    found[identity] = {'source': source, 'raw': raw, 'unit_id': unit['unit_id']}
                except (ValueError, KeyError, TypeError) as error:
                    record.update(status='FAIL', error=str(error))
                    audit['exceptions'].append(record.copy())
                audit['source_mappings'].append(record)
        packages[name] = found
        audit['datasets'][name] = {'source_assets': len(units), 'mapped_sources': len(found),
            'frozen_full_source_count': len(corpus) + len(files) if name != 'omnidocbench' else len(pages),
            'prepared_units_structurally_checked': normalized_count,
            'manifest_sha256': manifest_ref['sha256'], 'archive_sha256': archive_pin['sha256']}
    for name, dataset in dataset_map.items():
        full_ids = {d.doc_id for d in dataset.documents} | {d['doc_id'] for d in dataset.files}
        for case in dataset.cases:
            if set(case.qrels) - full_ids:
                raise ValueError('FROZEN_QRELS_OUTSIDE_FULL_CORPUS')
            for fact in (case.evaluation_metadata or {}).get('supporting_facts', []):
                if fact['document_id'] not in full_ids:
                    raise ValueError('SUPPORTING_FACT_OUTSIDE_FULL_CORPUS')
            audit['case_mappings'].append({'dataset': name, 'qid': case.qid,
                'positive_source_ids': sorted(positive_ids(case)),
                'positive_sources_missing_from_package': sorted(positive_ids(case) - packages[name].keys()),
                'all_qrels_mapped_to_full_corpus': True, 'answer_gold_count': len(case.answers)})
    for page in frozen.omni_pages:
        audit['omni_page_mappings'].append({'page_id': page['page_id'],
            'source_id': page['document_id'], 'image_sha256': page['image_sha256'],
            'parsing_gold_sha256': page['page_gold_sha256'], 'official_qa_gold': False,
            'package_mapped': page['document_id'] in packages['omnidocbench']})
    audit.update(source_assets=sum(d['source_assets'] for d in audit['datasets'].values()),
                 frozen_qa_cases=len(audit['case_mappings']), parsing_gold_pages=len(frozen.omni_pages))
    output_dir.mkdir(parents=True, exist_ok=True)
    if audit['exceptions']:
        audit['status'] = 'FAIL'
        targets[1].write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise ValueError('P8_SOURCE_MAPPING_OR_LEAKAGE_AUDIT_FAILED')
    selection = {'schema': 'p8-original-question-selection/v1', 'selection_rules': RULES, 'datasets': []}
    gold = {'schema': 'p8-selected-evaluation-gold/v1', 'evaluation_only': True,
            'manifest_sha256': manifest_sha256, 'datasets': []}
    for name in EXPECTED_UNITS:
        if name == 'omnidocbench':
            page = min(frozen.omni_pages, key=lambda p: p['page_id'])
            chosen = [{'qid': page['page_id'] + ':p8-diagnostic', 'question': OMNI_QUESTION}]
            ids = {page['document_id']}
            version = page['page_id'].split(':')[1]
            gold['datasets'].append({'name': name, 'version': version, 'cases': [],
                'diagnostic_cases': chosen, 'official_qa_gold': False,
                'qa_status': 'NOT_SCORED_DIAGNOSTIC_QUESTION_NO_INDEPENDENT_QA_GOLD',
                'pages': [page], 'parsing_gold': {page['page_id']: frozen.omni_gold[page['page_id']]}})
        else:
            dataset = dataset_map[name]
            selected, ids = choose_cases(name, dataset.cases, set(packages[name]), hot_contexts)
            chosen = [{'qid': c.qid, 'question': c.question} for c in selected]
            version = dataset.version
            gold['datasets'].append({'name': name, 'version': version, 'cases': [asdict(c) for c in selected]})
        files = []
        for identity in sorted(ids):
            entry = packages[name][identity]; source = entry['source']; raw = entry['raw']
            suffix = Path(source['file_name']).suffix
            destination = output_dir / 's' / name / (sha(raw)[:20] + suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                checked_bytes(destination, sha(raw), len(raw))
            else:
                with destination.open('xb') as stream:
                    stream.write(raw)
            files.append({'source_id': identity, 'path': str(destination.resolve()),
                'file_name': source['file_name'], 'media_type': source['media_type'], 'sha256': sha(raw)})
        selection['datasets'].append({'name': name, 'version': version, 'files': files, 'cases': chosen})
    verify_selection(selection)
    audit['selection_sha256'] = sha((json.dumps(selection, ensure_ascii=False, indent=2) + '\n').encode())
    audit['selected_counts'] = {d['name']: {'cases': len(d['cases']), 'source_assets': len(d['files'])} for d in selection['datasets']}
    for target, value in zip(targets, (selection, audit, gold)):
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-root', required=True)
    parser.add_argument('--freeze-manifest', required=True)
    parser.add_argument('--freeze-sha256', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    report = build(args.package_root, args.freeze_manifest, args.freeze_sha256, args.output_dir)
    print(json.dumps({key: report[key] for key in ('status', 'source_assets', 'frozen_qa_cases', 'parsing_gold_pages', 'selected_counts')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
