"""Read-only historical seven-image capacity replay; emits numeric evidence only."""
import argparse
import json
from pathlib import Path

from PIL import Image
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.input_bounds import deepseek_inline_image_input, deepseek_inline_image_context_input


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--media-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rows=json.loads((args.media_root/'seven-wire-facts.private.json').read_text(encoding='utf8'))
    config=provider_config('deepseek','deepseek-flash')
    result=[]
    for row in rows:
        body=json.loads(Path(row['wire_request_file']).read_text(encoding='utf8'))['body']
        wire=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
        context=deepseek_inline_image_context_input(config,body,wire,'vision')
        reservation=deepseek_inline_image_input(config,body,wire,'vision')
        image=Path(row['image_path'])
        with Image.open(image) as opened:dimensions=list(opened.size)
        result.append({'asset_sha_prefix':row['sha256'][:12], 'image_bytes':image.stat().st_size,
            'dimensions':dimensions,'wire_bytes':len(wire),'unchanged_reservation_input_upper':reservation,
            'context_input_upper':context,'max_output_tokens':body['max_tokens'],
            'declared_context_window':config.context_window,
            'context_fits':context+body['max_tokens']<=config.context_window})
    report={'status':'OFFLINE_PASS' if len(result)==7 and all(r['context_fits'] for r in result) else 'FAIL',
        'recipe':'deepseek-inline-image-context-1024/v1',
        'provenance':'saved exact historical wire+original image; no model calls; declared model capacity not live verified',
        'count':len(result),'rows':result}
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'status':report['status'],'count':len(result),'output':str(args.output)}))
    return 0 if report['status']=='OFFLINE_PASS' else 1


if __name__=='__main__':raise SystemExit(main())
