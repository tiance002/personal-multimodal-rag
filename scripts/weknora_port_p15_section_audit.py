"""P15 offline audit of saved MinerU section signals; no provider or DB imports."""
from __future__ import annotations
import argparse,collections,hashlib,json,os,re,subprocess
from pathlib import Path

BASE=Path('E:/WK/run/p11-real-20261016/B/assets/332/7f1d720d-9561-446f-a8cc-6a562c339d23')
P14=Path('E:/WK/run/p14-20261016')

def native(p):
    return Path('\\\\?\\'+str(p.resolve())) if os.name=='nt' else p

def load(p):return json.loads(native(p).read_bytes())
def sha(p):return hashlib.sha256(native(p).read_bytes()).hexdigest()
def walk(x,path=''):
    if isinstance(x,dict):
        yield path,x
        for k,v in x.items():yield from walk(v,path+'/'+k)
    elif isinstance(x,list):
        for i,v in enumerate(x):yield from walk(v,path+'/'+str(i))
def text(x):
    if isinstance(x,dict):
        if isinstance(x.get('content'),str):return x['content']
        return ''.join(text(v) for v in x.values() if isinstance(v,(dict,list)))
    if isinstance(x,list):return ''.join(text(v) for v in x)
    return ''
def pointer(x,path):
    for key in path.strip('/').split('/'):x=x[int(key)] if isinstance(x,list) else x[key]
    return x

def audit():
    proposal=load(P14/'offline-proposal.json');documents=[];middles={};files={}
    for binding in proposal['source_bindings']:
        root=BASE/binding['document_id']/binding['source_version'];outputs=[];middle=None
        for suffix in ['middle_json.json','model_output.json','structured_content.json']:
            candidates=list(root.rglob('*'+suffix));assert len(candidates)==1
            p=candidates[0];data=load(p);digest=sha(p);files[str(p)]=digest;keys=collections.Counter();headings=[];hits=[];relations=[]
            for path,node in walk(data):
                keys.update(node.keys())
                if node.get('type') in ['doc_title','paragraph_title']:
                    headings.append(dict(pointer=path,type=node['type'],level=node.get('level'),text=text(node),bbox=node.get('bbox')))
                for k,v in node.items():
                    if isinstance(v,str) and re.search(r'coding\s*time',v,re.I):hits.append(dict(pointer=path+'/'+k,text=v))
                    if re.search('section|parent|outline|heading|chapter|title|level',k,re.I):relations.append(dict(pointer=path+'/'+k,value=v))
            outputs.append(dict(path=str(p),sha256=digest,schema=data.get('schema'),schema_version=data.get('schema_version'),root_keys=list(data),all_key_counts=dict(keys),headings=headings,coding_time_hits=hits,relation_fields=relations))
            if suffix=='middle_json.json':
                assert digest in binding['middle_json_sha256'];assert data['metadata']['producer']==dict(name='mineru',version='4.0.12');middle=data;middles[binding['document_id']]=data
        original=list((root/'original').iterdir());assert len(original)==1 and sha(original[0]) in binding['source_sha256'];files[str(original[0])]=sha(original[0])
        documents.append(dict(**binding,original_path=str(original[0]),outputs=outputs,page_count=len(middle['pages'])))
    controls=[]
    for i in [14,30,6,24,28]:
        candidate=proposal['pool'][i];proof=candidate['provenance'];middle=middles[proof['document_id']];locators=proof['source_locators'];verified=[]
        for loc in locators:
            raw=loc['raw_evidence'];block=pointer(middle,raw['block_path']);start=loc['start']-raw['derived_block_start'];end=loc['end']-raw['derived_block_start'];assert 0<=start<end<=len(text(block));assert text(block)[start:end]==loc['raw_text'];assert middle['pages'][raw['page_idx']]['page_idx']+1==loc['page']
            verified.append(dict(pointer=raw['block_path'],page=loc['page'],start=loc['start'],end=loc['end'],raw_text=loc['raw_text'],block_type=raw['block_type'],middle_sha256=raw['middle']['sha256']))
        pages={page['page_idx']+1:page for page in middle['pages']}
        page_titles=[dict(page=n,pointer=f'/pages/{n-1}/blocks/{j}',type=b['type'],level=b.get('level'),text=text(b)) for n in proof['pages'] for j,b in enumerate(pages[n]['blocks']) if b['type'] in ['doc_title','paragraph_title']]
        controls.append(dict(slot=i,candidate_id=candidate['candidate_id'],provenance=proof,verified_locators=verified,titles_on_locator_pages=page_titles))
    cat=middles[proposal['pool'][14]['provenance']['document_id']];wireless=middles[proposal['pool'][30]['provenance']['document_id']]
    page33=cat['pages'][32];assert len(page33['blocks'])==1 and page33['blocks'][0]['type']=='image';assert text(page33)=='(picture by Dato)'
    assert not any(o['coding_time_hits'] for d in documents for o in d['outputs'])
    # Record real image provenance; do not OCR/transcribe it or infer its chapter title.
    catroot=BASE/proposal['pool'][14]['provenance']['document_id']/proposal['pool'][14]['provenance']['source_version']
    image=list(catroot.rglob('*page_32_image_0.jpg'));assert len(image)==1;files[str(image[0])]=sha(image[0])
    selected_pages=dict(row0434=[dict(pointer=f'/pages/{i}',page=cat['pages'][i]) for i in [31,32,33,34,35,36,37]],row0959=[dict(pointer=f'/pages/{i}',page=wireless['pages'][i]) for i in [15,16,17,18]])
    return dict(schema='p15-machine-section-offline-audit/v1',status='P15_COMPLETE_OFFLINE_STOP',source_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),documents=documents,controls=controls,selected_pages=selected_pages,page33_image=dict(path=str(image[0]),sha256=sha(image[0]),middle_pointer='/pages/32/blocks/0/content/0',logical_path='images/page_32_image_0.jpg'),input_hashes=files,decision=dict(reliable_0434_machine_section_association=False,reasons=['Coding time machine title absent in all three preserved output formats','physical33 image body text empty; footnote is not title','physical34 dataset title exists but no preserved parent/section relation to image-only page33','heading level and source page order alone do not establish the missing requested chapter; adjacency is not chapter recovery'],rerank='NOT_RUN_STOP_CONDITION',new_provider_requests=0,new_provider_tokens=0,new_unknown=0,production_integration=False),gold_loaded=False,new_parse_or_ocr=False)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert a.output.drive.upper()=='E:' and not a.output.exists();data=audit();a.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');print('OFFLINE_AUDIT_PASS documents2 outputs6 mapped_candidates5 STOP_NO_RELIABLE_0434_SECTION RERANK_NOT_RUN')
if __name__=='__main__':main()
