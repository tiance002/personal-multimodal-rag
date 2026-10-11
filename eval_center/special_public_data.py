"""Frozen public Hotpot/MML adapters; raw sources and evaluation Gold stay separate."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
from eval_center.public_data import PublicCase, PublicDataset, PublicDocument

SPECIAL_QA_DATASETS=('hotpotqa','mmlongbench-doc')

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def verified_bytes(path,record):
    payload=Path(path).read_bytes()
    if len(payload)!=record['bytes'] or hashlib.sha256(payload).hexdigest()!=record['sha256']:
        raise ValueError('special_frozen_file_hash_mismatch')
    return payload

def source_path(record,asset_root=None):
    return Path(asset_root)/record['relative_path'] if asset_root is not None else Path(record['local_path'])

def load_special_datasets(freeze_path,*,expected_sha256,asset_root=None):
    freeze_path=Path(freeze_path);raw=freeze_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('special_freeze_manifest_hash_mismatch')
    freeze=json.loads(raw)
    if freeze.get('schema')!='v6-special-subsets-content-freeze-v1' or freeze.get('source_selection_changed_after_content_download') is not False:
        raise ValueError('special_freeze_manifest_not_accepted')
    rows={}
    for record in freeze['small_uploaded_files']:
        if record['name'] not in {'hotpot-selected8.jsonl','mml-selected8.jsonl'}:continue
        path=Path(asset_root)/record['name'] if asset_root else freeze_path.parent.parent/record['name']
        rows[record['name']]=[json.loads(line) for line in verified_bytes(path,record).decode().splitlines() if line.strip()]
    if any(len(rows.get(name,[]))!=8 for name in ('hotpot-selected8.jsonl','mml-selected8.jsonl')):
        raise ValueError('special_eight_questions_required')
    contexts=[];hot_cases=[]
    for row in rows['hotpot-selected8.jsonl']:
        local={}
        titles,sentences=row['context']['title'],row['context']['sentences']
        if len(titles)!=len(sentences):raise ValueError('hotpot_context_alignment')
        for title,sents in zip(titles,sentences):
            key=hashlib.sha256((title+'\0'+json.dumps(sents,ensure_ascii=False,separators=(',',':'))).encode()).hexdigest()
            doc_id='hotpotqa-context:'+key;local[title]=doc_id
            contexts.append({'source_document_id':doc_id,'title':title,'sentences':sents,'text':'\n'.join(sents),
                'source_qid':row['id'],'role':'full_original_context; not supporting-facts-filtered'})
        facts=[]
        for title,sent in zip(row['supporting_facts']['title'],row['supporting_facts']['sent_id']):
            if title not in local or type(sent) is not int or not 0<=sent<len(sentences[titles.index(title)]):
                raise ValueError('hotpot_supporting_fact_out_of_source')
            facts.append({'document_id':local[title],'title':title,'sentence_index_zero_based':sent})
        hot_cases.append(PublicCase(row['id'],row['question'],{f['document_id']:1 for f in facts},'development',
            (row['answer'],),qrels_kind='SOURCE_DERIVED_SUPPORTING_FACT_DOCUMENT_PROJECTION',
            evaluation_metadata={'gold_source':'OFFICIAL_NATIVE_ANSWER_AND_SUPPORTING_FACTS','supporting_facts':facts,
                'original_split':'validation_distractor','answer_scorer':'hotpot_answer_normalization_em_token_f1'}))
    corpus=('\n'.join(json.dumps(c,ensure_ascii=False,separators=(',',':')) for c in contexts)+'\n').encode()
    context_record=next(r for r in freeze['derived_files'] if r['path'].endswith('hotpot-context73.jsonl'))
    if len(corpus)!=context_record['bytes'] or hashlib.sha256(corpus).hexdigest()!=context_record['sha256']:
        raise ValueError('hotpot_full_context_identity_mismatch')
    if [c.qid for c in hot_cases]!=freeze['hotpot_native_QIDs']:raise ValueError('hotpot_native_qids_changed')
    hot_docs=tuple(PublicDocument(c['source_document_id'],c['title'],c['text']) for c in contexts)
    pdfs=[r for r in freeze['source_files'] if r['dataset']=='MMLongBench-Doc']
    pdf_sources=[]
    for record in pdfs:
        path=source_path(record,asset_root)
        verified_bytes(path,{'bytes':record['actual_bytes'],'sha256':record['actual_sha256']})
        pdf_sources.append({'doc_id':Path(record['relative_path']).name,'path':str(path),
            'bytes':record['actual_bytes'],'sha256':record['actual_sha256'],'source_kind':'FULL_PDF_ALL_PAGES'})
    doc_ids={r['doc_id'] for r in pdf_sources};mml_cases=[]
    mapping={r['qid']:r for r in freeze['mml_QID_document_page_mapping']}
    for row in rows['mml-selected8.jsonl']:
        original=row['original_row'];pages=ast.literal_eval(original['evidence_pages'])
        if original['doc_id'] not in doc_ids or pages!=mapping[row['qid']]['original_evidence_pages']:
            raise ValueError('mml_document_page_gold_changed')
        mml_cases.append(PublicCase(row['qid'],original['question'],{original['doc_id']:1},'development',
            (original['answer'],),qrels_kind='SOURCE_DERIVED_OFFICIAL_DOCUMENT_IDENTITY',
            evaluation_metadata={'gold_source':'OFFICIAL_NATIVE_TYPED_ANSWER','original_row_index':row['row_idx'],
                'answer_format':original['answer_format'],'evidence_pages':pages,
                'evidence_sources':original['evidence_sources'],'page_index_convention':'UNKNOWN',
                'official_gpt_normalization':'NOT_RUN','parsing_gold':'NOT_AVAILABLE'}))
    versions={'hotpotqa':'HotpotQA-'+freeze['dataset_versions']['HotpotQA']['serving_revision'],
        'mmlongbench-doc':'MMLongBench-Doc-'+freeze['dataset_versions']['MMLongBench-Doc']}
    payloads={'hotpotqa':(hot_docs,tuple(hot_cases),contexts),
        'mmlongbench-doc':(tuple(PublicDocument(r['doc_id'],r['doc_id'],'') for r in pdf_sources),tuple(mml_cases),pdf_sources)}
    result={}
    for name,(docs,cases,sources) in payloads.items():
        manifest={'schema_version':'frozen-special-public-v1','dataset':name,'dataset_version':versions[name],
            'freeze_manifest_sha256':expected_sha256,'freeze_manifest_path':str(freeze_path.resolve()),
            'asset_root':str(Path(asset_root).resolve()) if asset_root else None,
            'source_payload':sources,'source_payload_sha256':hashlib.sha256(canonical(sources).encode()).hexdigest(),
            'raw_pdf_ingestion_required':name=='mmlongbench-doc','all_sources_shared_across_qids':True}
        result[name]=PublicDataset(name,versions[name],'development',manifest,docs,cases)
    return result

def case_row(dataset,case):
    row={'dataset':dataset.name,'dataset_version':dataset.dataset_version,'qid':case.qid,
        'question':case.question,'qrels':case.qrels,'split':'development','answers':list(case.answers),
        'qrels_kind':case.qrels_kind,'evaluation_metadata':case.evaluation_metadata}
    return row

def prepare_full_cohort(*,base_cohort_path,base_source_path,freeze_path,freeze_sha256,output_dir,asset_root=None):
    """Write NEW hash-frozen adapter artifacts; never alter original cohorts/Gold."""
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    def save(name,payload):
        path=output/name;data=payload.encode()
        if path.exists():
            if path.read_bytes()!=data:raise ValueError('special_preparation_identity_conflict')
        else:path.write_bytes(data)
        return {'path':str(path.resolve()),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    special=load_special_datasets(freeze_path,expected_sha256=freeze_sha256,asset_root=asset_root)
    base=[json.loads(line) for line in Path(base_cohort_path).read_text(encoding='utf-8').splitlines() if line.strip()]
    from eval_center.paired_state import load_frozen_cohort
    if len(load_frozen_cohort(base_cohort_path))!=24:raise ValueError('frozen_base24_required')
    source=json.loads(Path(base_source_path).read_text(encoding='utf-8'))
    if source['status']!='FROZEN_FOR_PREPARATION':raise ValueError('base_source_not_frozen')
    source['base_cohort_sha256']=hashlib.sha256(Path(base_cohort_path).read_bytes()).hexdigest()
    source['base_source_manifest_sha256']=hashlib.sha256(Path(base_source_path).read_bytes()).hexdigest()
    source['special_freeze_manifest_sha256']=freeze_sha256
    from eval_center.omni_contract import page_input_contract
    omni=page_input_contract(freeze_path,expected_sha256=freeze_sha256,asset_root=asset_root)
    source['omni_pages']=save('omni-page-input-contract.json',canonical(omni)+'\n')
    source['omni_page_count']=omni['page_count']
    for name,dataset in special.items():
        selected=[case_row(dataset,c) for c in dataset.cases];base.extend(selected)
        corpus=save(name+'-sources.json',canonical(dataset.manifest['source_payload'])+'\n')
        cases=save(name+'-cases.jsonl',''.join(canonical(r)+'\n' for r in selected))
        source['datasets'].append({'dataset':name,'dataset_version':dataset.dataset_version,
            'adapter':'frozen_special','freeze_path':str(Path(freeze_path).resolve()),'freeze_sha256':freeze_sha256,
            'asset_root':dataset.manifest['asset_root'],'corpus':corpus,'development_cases':cases,
            'source_manifest':{'path':str(Path(freeze_path).resolve()),'sha256':freeze_sha256},
            'candidate_pool':{'source_document_ids':[d.doc_id for d in dataset.documents],
                'scope':'FULL_FROZEN_SHARED_SOURCES_NO_GOLD_FILTER'},'parsing_gold':'NOT_AVAILABLE'})
    if len(base)!=40:raise ValueError('full_40_questions_required')
    cohort=save('cohort_dev40.jsonl',''.join(canonical(r)+'\n' for r in base))
    manifest=save('source_manifest_40.json',canonical(source)+'\n')
    load_frozen_cohort(Path(cohort['path']))
    return {'status':'FROZEN_FOR_PREPARATION','cohort':cohort,'source_manifest':manifest,
        'question_count':40,'omni_page_count':24,'omni_official_qa_count':0,
        'model_calls':0,'index_ingestion':'NOT_RUN','six_dataset_rag_acceptance':'NOT_RUN'}
