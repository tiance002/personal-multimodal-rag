import hashlib,json
from pathlib import Path
import pytest
from eval_center.weknora_source_packages import load_originals


def package(tmp_path):
    content=b"Actual source text";sha=hashlib.sha256(content).hexdigest()
    (tmp_path/'objects').mkdir();(tmp_path/'objects'/sha).write_bytes(content)
    unit={'unit_id':'one','source':{'source_document_id':'doc','file_name':'source.txt','media_type':'text/plain',
        'artifact':{'path':'objects/'+sha,'sha256':sha,'bytes':len(content)},'provenance':{'source_revision':'frozen'}}}
    doc={'schema':'rag-v6-file-transport/v1','dataset':'hotpotqa','Gold_qrels_answers_included':False,
        'units':[dict(unit,prepared={'chunks_json':{'path':'DO_NOT_READ_GOLD.json'}})]}
    path=tmp_path/'manifest.json';path.write_text(json.dumps(doc));return path,doc

def write(path,doc):
    path.write_text(json.dumps(doc));return hashlib.sha256(path.read_bytes()).hexdigest()

def test_original_only_no_prepared_gold_dependency(tmp_path):
    path,doc=package(tmp_path);assets=load_originals(path,write(path,doc),'hotpotqa')
    assert len(assets)==1 and assets[0].read()==b'Actual source text'
    Path(assets[0].path).write_bytes(b'changed')
    with pytest.raises(ValueError,match='SOURCE_BYTES_IDENTITY_MISMATCH'):assets[0].read()

@pytest.mark.parametrize('ref',['../Gold.json','prepared/chunks.json','C:/Gold.json','objects/../Gold.json'])
def test_outside_or_prepared_refs_rejected(tmp_path,ref):
    path,doc=package(tmp_path);doc['units'][0]['source']['artifact']['path']=ref
    with pytest.raises(ValueError,match='SOURCE_OBJECT_REF_INVALID'):load_originals(path,write(path,doc),'hotpotqa')

def test_gold_unknown_and_manifest_tampering_rejected(tmp_path):
    path,doc=package(tmp_path);sha=write(path,doc);doc['Gold_qrels_answers_included']=True;write(path,doc)
    with pytest.raises(ValueError,match='SOURCE_BYTES_IDENTITY_MISMATCH'):load_originals(path,sha,'hotpotqa')
    with pytest.raises(ValueError,match='SOURCE_GOLD_BOUNDARY_UNKNOWN'):load_originals(path,write(path,doc),'hotpotqa')
