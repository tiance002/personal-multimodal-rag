"""New boundary fixtures; reuse hash-bound original Go collector/enrich/type."""
from pathlib import Path
import json,os,subprocess,sys,hashlib
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/weknora-port/evidence/IMAGE_JSON_BOUNDARY_20261015'
ORIGINAL=ROOT/'docs/weknora-port/evidence/SEQ_IMAGE_COMPLETION_20261015'
WORK=Path(r'E:\WK\run\image-json-boundary-20261015');WORK.mkdir(parents=True,exist_ok=True)
GO=Path(r'E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe')
cases=[]
def add(name,raw):cases.append({'name':name,'raw':raw})
add('uppercase','[{"URL":"images/a.png","Caption":"Uppercase caption","OCR_Text":"Uppercase OCR"}]')
add('all-mixed-fields','[{"uRl":"a","ORIGINAL_URL":"o","StArT_PoS":-3,"END_POS":8,"cApTiOn":"C","OcR_TeXt":"O"}]')
add('case-later-wins','[{"url":"first","URL":"last"}]')
add('exact-later-wins','[{"URL":"first","url":"last"}]')
add('repeated-exact-order','[{"url":"first","URL":"middle","url":"last"}]')
add('repeated-case-order','[{"URL":"first","url":"middle","URL":"last"}]')
add('null-keeps-primitives','[{"url":"a","URL":null,"caption":"keep","Caption":null,"start_pos":7,"START_POS":null}]')
add('null-before-values','[{"URL":null,"url":"a","start_pos":null,"Start_Pos":0}]')
for n in [0,-1,-9223372036854775808,9223372036854775807,-9223372036854775809,9223372036854775808]:
 add('int-'+str(n),json.dumps([{'url':'a','start_pos':n,'end_pos':n}]))
add('wrong-then-valid-still-rejected','[{"url":"a","start_pos":false,"START_POS":0}]')
add('valid-then-wrong-rejected','[{"url":"a","start_pos":0,"Start_Pos":"1"}]')
add('wrong-string-then-valid','[{"URL":false,"url":"a"}]')
add('bad-second-item-invalidates-array','[{"url":"a"},{"URL":"b","END_POS":9223372036854775808}]')
add('fraction-not-int','[{"URL":"a","Start_Pos":1.0}]')
add('exponent-not-int','[{"url":"a","start_pos":0e0}]')
add('bool-not-int','[{"url":"a","start_pos":true}]')
add('object-not-string','[{"url":"a","CAPTION":{}}]')
add('null-array-item','[null,{"URL":"a","Caption":null,"end_pos":null}]')
add('unknown-fields-ignored','[{"URL":"a","page_number":9223372036854775808,"binding":{"URL":false}}]')
add('root-null','null');add('root-object','{"URL":"a"}');add('root-empty-array','[]')
add('array-scalar','[{"URL":"a"},false]')
add('unicode-simple-fold-s','[{"URL":"a","\u017ftart_po\u017f":9}]')
add('no-unicode-full-casefold-expansion','[{"URL":"a","\ufb06art_pos":9}]')
inputs=[dict(name=c['name'],children=[dict(id='child',parent_chunk_id='parent',chunk_type='image_ocr',image_info=c['raw'],is_enabled=True)],results=[dict(id='parent',image_info='')],fail_at=0) for c in cases]
source=(ORIGINAL/'oracle.go').read_bytes()
assert hashlib.sha256(source).hexdigest()==json.loads((ORIGINAL/'source-binding.json').read_text(encoding='utf-8'))['evidence_sha256']['oracle.go']
file=WORK/'oracle.go';file.write_bytes(source)
env=dict(os.environ)
for k,v in [('GOCACHE',Path(r'E:\WK\run\seq-image-completion-20261015\go\gocache')),('GOTMPDIR',WORK/'tmp'),('GOPATH',WORK/'gopath'),('GOMODCACHE',WORK/'modcache'),('TEST_TELEMETRY_DIR',WORK/'telemetry')]:v.mkdir(parents=True,exist_ok=True);env[k]=str(v)
env['TMP']=env['TEMP']=str(WORK/'tmp');env.update(GOENV='off',GO111MODULE='off',GOTOOLCHAIN='local',GOPROXY='off',GOSUMDB='off',CGO_ENABLED='0',GOEXPERIMENT='')
(WORK/'telemetry/mode').write_text('off',encoding='utf-8')
cmd=[str(GO),'run',str(file)]
assert not (OUT/'go.exitcode.txt').exists()
(OUT/'go.command.json').write_text(json.dumps({'argv':cmd,'cwd':str(WORK),'env':{k:env[k] for k in ['GOCACHE','GOTMPDIR','GOPATH','GOMODCACHE','TEST_TELEMETRY_DIR','TEMP','TMP','GOENV','GO111MODULE','GOTOOLCHAIN','GOPROXY','GOSUMDB','CGO_ENABLED','GOEXPERIMENT']}},indent=2)+'\n',encoding='utf-8')
p=subprocess.run(cmd,cwd=WORK,env=env,input=json.dumps(inputs).encode(),capture_output=True)
(OUT/'go.stdout.txt').write_bytes(p.stdout);(OUT/'go.stderr.txt').write_bytes(p.stderr);(OUT/'go.exitcode.txt').write_text(str(p.returncode)+'\n',encoding='utf-8')
if p.returncode:print(p.stderr.decode(errors='replace'));sys.exit(p.returncode)
rows=json.loads(p.stdout)
fixture={'upstream_commit':'3e8b0bfc80b845b2d4b2ed683994748741450a97','oracle_program_sha256':hashlib.sha256(source).hexdigest(),'scope':'existing source-extracted collector/enrich/type; actual Go encoding/json','exit_code':0,'cases':[dict(c,expected_parent=row['results'][0]['image_info']) for c,row in zip(cases,rows)]}
(ROOT/'tests/weknora_port/fixtures/image_json_boundary_go_oracle.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('GO_JSON_BOUNDARY_CASES',len(cases),'EXIT',p.returncode)
