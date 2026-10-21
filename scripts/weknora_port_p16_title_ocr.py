"""P16 single real local OCR of the unchanged saved page33 image, with provenance."""
from __future__ import annotations
import argparse,hashlib,io,json,time,subprocess,importlib.metadata
from dataclasses import asdict
from pathlib import Path
from backend.weknora_port.tesseract_ocr import TesseractOCR
from backend.weknora_port.documents import _native_io_path

def load(p):return json.loads(_native_io_path(p).read_bytes())
def sha(p):return hashlib.sha256(_native_io_path(p).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();assert args.output.drive.upper()=='E:' and not args.output.exists()
    audit=load(Path('E:/WK/run/p15-20261016/section-audit.json'));source=audit['page33_image'];path=Path(source['path']);assert sha(path)==source['sha256'];doc=next(d for d in audit['documents'] if d['document_id']=='4019d2aa-c21d-42c2-8e8f-4d5aee4039ce');middle_ref=next(o for o in doc['outputs'] if o['path'].endswith('middle_json.json'));middle=load(Path(middle_ref['path']));assert sha(Path(middle_ref['path']))==middle_ref['sha256'];page=middle['pages'][32];node=page['blocks'][0]['content'][0];assert node['type']=='image_body' and node['image_path']=='images/page_32_image_0.jpg' and node['content']==''
    from PIL import Image
    data=_native_io_path(path).read_bytes()
    with Image.open(io.BytesIO(data)) as img:width,height=img.size;assert img.format=='JPEG'
    tess=Path('E:/WK/deps/tessdata');datahash={p.name:sha(p) for p in [tess/'eng.traineddata',tess/'chi_sim.traineddata']}
    # Exactly the production adapter configuration, original image bytes, no special OCR tuning.
    start=time.monotonic();result=TesseractOCR(tessdata=tess).recognize(data,'image/jpeg');elapsed=time.monotonic()-start
    x0,y0,x1,y1=node['bbox'];mapped=[]
    for line in result.layout.get('lines',[]):
        assert result.text[line['start']:line['end']]==line['text'];a,b,c,d=line['bbox'];assert 0<=a<c<=width and 0<=b<d<=height
        bbox=[x0+a/width*(x1-x0),y0+b/height*(y1-y0),x0+c/width*(x1-x0),y0+d/height*(y1-y0)]
        mapped.append(dict(**line,source_image_sha256=source['sha256'],pdf_page=33,pdf_page_idx=32,page_bbox_normalized_approx=bbox,page_bbox_points_approx=[bbox[0]*768,bbox[1]*576,bbox[2]*768,bbox[3]*576]))
    record=dict(schema='p16-real-local-ocr/v1',source_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),adapter='backend.weknora_port.tesseract_ocr.TesseractOCR.recognize',adapter_sha256=sha(Path('backend/weknora_port/tesseract_ocr.py')),real_local_ocr_calls=1,new_provider_requests=0,new_provider_tokens=0,new_unknown=0,configuration=dict(language='eng+chi_sim',dpi=150,full=True,tessdata=str(tess),traineddata_sha256=datahash,pymupdf=importlib.metadata.version('pymupdf'),pillow=importlib.metadata.version('pillow')),input=dict(document_id=doc['document_id'],source_version=doc['source_version'],kb_id=doc['kb_id'],source_pdf_path=doc['original_path'],source_pdf_sha256=doc['source_sha256'][0],image_path=str(path),image_sha256=source['sha256'],image_dimensions=dict(width=width,height=height),middle_path=middle_ref['path'],middle_sha256=middle_ref['sha256'],middle_pointer=source['middle_pointer'],logical_path=source['logical_path'],physical_page=33,normalized_image_bbox=node['bbox'],page_dimensions_points=dict(width=768,height=576)),raw_ocr=asdict(result),mapped_lines=mapped,seconds=elapsed,coordinate_caveat='Crop pixels exact in OCR layout. Page projection uses existing rounded MinerU bbox; approximate, not pixel-exact original PDF geometry.',source_version_not_mutated=True,gold_loaded=False,manual_title_added=False,image_preprocessing=False)
    args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(dict(status='OCR_ERROR' if result.error else 'OCR_RECORDED',error=result.error,text=result.text,lines=len(mapped),seconds=elapsed),ensure_ascii=False))
if __name__=='__main__':main()
