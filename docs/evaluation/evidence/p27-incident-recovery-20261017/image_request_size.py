"""Measure actual target-page bytes with the existing renderer; sends nothing."""
import base64, hashlib, io, json, struct, sys
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013')
sys.path.insert(0,str(ROOT))
import pypdfium2 as pdfium
from PIL import Image
from backend.weknora_port.ingestion.upstream_pdf_parser import CONFIG, _render_scanned_pages, _normalize_image_quality
from backend.weknora_port.models import provider_config, ChatOptions, Message, build_chat_body
from backend.weknora_port.image_multimodal import caption_prompt
from backend.weknora_port.source_facts import _deepseek_inline_image_input

out=Path(__file__).parent
manifest=json.loads(Path('E:/WK/run/p27-20261017/offline_parser_asset_inventory.json').read_text())
rows=[]
config=provider_config('deepseek','deepseek-flash',model_type='VLLM')
for qid,page in [('row-0958',9),('row-0684',10)]:
    source=next(r for r in manifest['cases'] if r['qid']==qid)
    raw=Path(source['source_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==source['expected_sha256']
    pdf=pdfium.PdfDocument(raw)
    try:
        # Target page only, identical render parameters to the normal parser.
        image=_render_scanned_pages(pdf,raw,[page-1],max(1,CONFIG.pdf_render_dpi)/72,
            _normalize_image_quality(CONFIG.pdf_jpeg_quality),CONFIG.pdf_render_max_edge)[page-1]
    finally: pdf.close()
    path=out/(qid+'-p'+str(page)+'.jpg')
    with path.open('xb') as f: f.write(image)
    url='data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')
    options=ChatOptions(max_tokens=5000,temperature=float(struct.unpack('f',struct.pack('f',.1))[0]))
    body=build_chat_body(config,(Message('user',caption_prompt('Chinese'),images=(url,)),),options)
    wire=json.dumps(body,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode()
    bound=_deepseek_inline_image_input(config,body,wire,'vision')
    with Image.open(io.BytesIO(image)) as im: dimensions=list(im.size)
    rows.append(dict(qid=qid,physical_page=page,source_sha256=source['expected_sha256'],
        image_path=str(path),image_sha256=hashlib.sha256(image).hexdigest(),dimensions=dimensions,
        image_bytes=len(image),base64_chars=len(url)-len('data:image/jpeg;base64,'),
        request_wire_bytes=len(wire),wire_sha256=hashlib.sha256(wire).hexdigest(),
        max_tokens=body.get('max_tokens'),thinking=body.get('thinking'),
        input_reservation=bound,output_reservation=5000,total_reservation=bound+5000,
        fits_1000000=bound+5000<=1_000_000,
        scope='offline target render and proposed default Caption request; not a sent request or ingestion'))
result=dict(schema='p27-actual-image-request-size/v1',provider_calls=0,cases=rows,
    sum_two_reservations=sum(r['total_reservation'] for r in rows))
with (out/'image-request-size.json').open('x',encoding='utf-8') as f: json.dump(result,f,indent=2)
print(json.dumps(result,indent=2))
