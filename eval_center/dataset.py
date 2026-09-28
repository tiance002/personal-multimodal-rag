"""Validate frozen stable-source Gold without altering legacy fixture schema."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval_center.gold import evidence_from_dict


def load_reviewed_dataset(directory:Path):
    directory=Path(directory)
    manifest=json.loads((directory/'sources.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!='stable-gold-v1':
        raise ValueError('unsupported stable Gold dataset schema')
    raw=(directory/'locked.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['locked_sha256']:
        raise ValueError('locked dataset changed')
    sources={}
    for source in manifest['sources']:
        path=(directory/source['path']).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError('source path escapes dataset')
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=source['source_version']:
            raise ValueError('frozen source changed')
        if source['document_id'] in sources:
            raise ValueError('duplicate document identity')
        sources[source['document_id']]=(source,payload.decode('utf-8'))
    cases=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if len(cases)!=manifest['sample_count'] or len({case['case_id'] for case in cases})!=len(cases):
        raise ValueError('invalid sample count or duplicate case ID')
    for case in cases:
        if case['dataset_version']!=manifest['dataset_version'] or case['split']!='locked_test':
            raise ValueError('dataset identity mismatch')
        if not isinstance(case['question'],str) or not case['question'].strip():
            raise ValueError('invalid question')
        for field in ('expected_chunk_ids','answer_points','kb_scope'):
            if not isinstance(case[field],list) or any(not isinstance(item,str) or not item for item in case[field]):
                raise ValueError('legacy fields must remain string lists')
        if not case['kb_scope'] or case['expected_chunk_ids']:
            raise ValueError('stable Gold must not depend on transient chunks')
        gold=[evidence_from_dict(item) for item in case['gold_evidence']]
        if type(case['answerable']) is not bool or case['answerable']!=bool(gold):
            raise ValueError('answerability mismatch')
        if len({item.evidence_id for item in gold})!=len(gold):
            raise ValueError('duplicate Gold evidence')
        for item,annotation in zip(gold,case['gold_evidence']):
            source,content=sources[item.span.document_id]
            if item.span.source_version!=source['source_version']:
                raise ValueError('wrong source version')
            if item.span.end>len(content) or content[item.span.start:item.span.end]!=annotation['reviewed_quote']:
                raise ValueError('Gold quote does not match frozen source')
        if any(not isinstance(group,list) or not group or any(not isinstance(point,str) or not point.strip() for point in group)
               for group in case['required_answer_points']):
            raise ValueError('invalid reviewed answer points')
    return manifest,cases,sources
