"""Read-only frozen public source/QA bindings for the native port, no old executor."""
from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
import hashlib,json
from eval_center.public_data import PublicCase,PublicDocument,DATASET_VERSIONS
from eval_center.special_public_data import canonical
from eval_center.weknora_source_packages import checked_bytes

@dataclass(frozen=True)
class NativeDataset:
    name:str
    version:str
    cases:tuple[PublicCase,...]=field(repr=False)
    documents:tuple[PublicDocument,...]=field(default=(),repr=False)
    files:tuple[dict,...]=field(default=(),repr=False)

@dataclass(frozen=True)
class FrozenGold:
    manifest_sha256:str
    datasets:tuple[NativeDataset,...]=field(repr=False)
    omni_pages:tuple[dict,...]=field(repr=False)
    omni_gold:dict=field(repr=False)
    source_receipts:tuple[dict,...]

    def summary(self):
        mml=next(d for d in self.datasets if d.name=='mmlongbench-doc')
        return {'schema':'native-frozen-six-dataset-bindings-v1','manifest_sha256':self.manifest_sha256,
            'qa_cases':{d.name:len(d.cases) for d in self.datasets},'omni_pages':len(self.omni_pages),
            'mml_page_references':sum(len(c.evaluation_metadata.get('evidence_pages',[])) for c in mml.cases),
            'mml_page_base':'UNKNOWN','omni_qa':'NOT_RUN_INDEPENDENT_OVERLAY_UNAVAILABLE',
            'omni_parsing_gold':'VERIFIED_EVALUATION_ONLY','p7_native_execution':'NOT_RUN',
            'old_predictions_used':False,'legacy_grants_loaded':False,'source_receipts':self.source_receipts}


def load_frozen_gold(manifest_path,expected_sha256):
    receipts=[]
    def read(record):
        path=Path(record['path']);raw=checked_bytes(path,record['sha256'],record.get('bytes'))
        receipts.append({'path':str(path),'sha256':record['sha256'],'bytes':len(raw)})
        return raw
    manifest=json.loads(read({'path':str(manifest_path),'sha256':expected_sha256}))
    if manifest.get('schema_version')!='p7-v6-dev24-freeze-v1' or manifest.get('status')!='FROZEN_FOR_PREPARATION':
        raise ValueError('NATIVE_SOURCE_FREEZE_NOT_ACCEPTED')
    datasets=[];freeze=None
    for row in manifest['datasets']:
        name=row['dataset'];version=row['dataset_version']
        raw_cases=[json.loads(line) for line in read(row['development_cases']).decode().splitlines() if line.strip()]
        selected=row.get('selected_qids')
        if selected is None:selected=[c['qid'] for c in raw_cases]
        mapping={c['qid']:c for c in raw_cases}
        if len(mapping)!=len(raw_cases) or len(selected)!=8 or len(set(selected))!=8 or set(selected)-mapping.keys():
            raise ValueError('NATIVE_FROZEN_QID_IDENTITY_MISMATCH')
        corpus=read(row['corpus']);documents=[];files=[]
        if name in DATASET_VERSIONS:
            if version!=DATASET_VERSIONS[name]:raise ValueError('NATIVE_DATASET_VERSION_MISMATCH')
            source_manifest=json.loads(read(row['source_manifest']))
            if source_manifest.get('dataset')!=name or source_manifest.get('dataset_version')!=version:
                raise ValueError('NATIVE_CORPUS_MANIFEST_MISMATCH')
            for line in corpus.decode().splitlines():
                if not line.strip():continue
                doc=json.loads(line)
                if set(doc)-{'doc_id','title','text'}:raise ValueError('NATIVE_CORPUS_GOLD_BOUNDARY_VIOLATION')
                documents.append(PublicDocument(doc['doc_id'],doc['title'],doc['text']))
        elif name=='hotpotqa':
            contexts=json.loads(corpus)
            if len(contexts)!=73:raise ValueError('NATIVE_HOTPOT_FULL_CONTEXT_COUNT')
            docs={}
            for doc in contexts:
                key=hashlib.sha256((doc['title']+'\0'+json.dumps(doc['sentences'],ensure_ascii=False,separators=(',',':'))).encode()).hexdigest()
                if doc['source_document_id']!='hotpotqa-context:'+key or doc['text']!='\n'.join(doc['sentences']):
                    raise ValueError('NATIVE_HOTPOT_ORIGINAL_CONTEXT_CHANGED')
                if not doc['role'].startswith('full_original_context'):raise ValueError('NATIVE_HOTPOT_GOLD_FILTERED_CORPUS')
                docs[doc['source_document_id']]=doc
                documents.append(PublicDocument(doc['source_document_id'],doc['title'],doc['text']))
            freeze=json.loads(read(row['source_manifest']))
            if selected!=freeze['hotpot_native_QIDs']:raise ValueError('NATIVE_HOTPOT_QIDS_CHANGED')
            for qid in selected:
                for fact in mapping[qid]['evaluation_metadata']['supporting_facts']:
                    doc=docs[fact['document_id']];index=fact['sentence_index_zero_based']
                    if type(index) is not int or not 0<=index<len(doc['sentences']):raise ValueError('NATIVE_HOTPOT_FACT_OUTSIDE_SOURCE')
        elif name=='mmlongbench-doc':
            if freeze is None:freeze=json.loads(read(row['source_manifest']))
            files=json.loads(corpus)
            if len(files)!=3 or any(d['source_kind']!='FULL_PDF_ALL_PAGES' for d in files):raise ValueError('NATIVE_MML_FULL_PDF_REQUIRED')
            for record in files:read(record)
            pages={c['qid']:c for c in freeze['mml_QID_document_page_mapping']}
            for qid in selected:
                case=mapping[qid]
                if case['evaluation_metadata']['evidence_pages']!=pages[qid]['original_evidence_pages']:
                    raise ValueError('NATIVE_MML_PAGE_GOLD_CHANGED')
                if case['evaluation_metadata']['page_index_convention']!='UNKNOWN':raise ValueError('NATIVE_MML_PAGE_BASE_PROOF_REQUIRED')
        else:raise ValueError('NATIVE_DATASET_UNDECLARED')
        ids={d.doc_id for d in documents}|{d['doc_id'] for d in files}
        cases=[]
        for qid in selected:
            c=mapping[qid]
            if set(c['qrels'])-ids:raise ValueError('NATIVE_QRELS_OUTSIDE_CORPUS')
            cases.append(PublicCase(qid,c['question'],c['qrels'],c['split'],tuple(c.get('answers',())),
                paired_source_doc_id=c.get('paired_source_doc_id'),qrels_kind=c.get('qrels_kind','official_document_qrels'),evaluation_metadata=c.get('evaluation_metadata') or {}))
        datasets.append(NativeDataset(name,version,tuple(cases),tuple(documents),tuple(files)))
    contract=json.loads(read(manifest['omni_pages']))
    pages=contract['pages']
    if len(pages)!=24 or freeze is None:raise ValueError('NATIVE_OMNI_FIXED_PAGES_REQUIRED')
    gold_record=next(r for r in freeze['source_files'] if r['relative_path'].endswith('/OmniDocBench.json'))
    gold_rows=json.loads(read({'path':gold_record['local_path'],'sha256':gold_record['actual_sha256'],'bytes':gold_record['actual_bytes']}))
    gold={}
    for page in pages:
        read({'path':page['image_path'],'sha256':page['image_sha256']})
        raw=gold_rows[page['gold_row_index']]
        if hashlib.sha256(canonical(raw).encode()).hexdigest()!=page['page_gold_sha256']:raise ValueError('NATIVE_OMNI_PAGE_GOLD_CHANGED')
        if page['gold_role']!='EVALUATION_ONLY_NOT_INGESTION':raise ValueError('NATIVE_OMNI_GOLD_BOUNDARY_UNKNOWN')
        gold[page['page_id']]=raw
    return FrozenGold(expected_sha256,tuple(datasets),tuple(pages),gold,tuple(receipts))


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args();bundle=load_frozen_gold(args.manifest,args.sha256)
    Path(args.report).write_text(json.dumps(bundle.summary(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'qa':{d.name:len(d.cases) for d in bundle.datasets},'omni_parsing_pages':len(bundle.omni_pages),'native_model_execution':'NOT_RUN'}))

if __name__=='__main__':main()
