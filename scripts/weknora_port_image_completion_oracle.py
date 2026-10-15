"""Execute pinned image collector/enrich bodies with typed fixture repository only.

Tencent/WeKnora MIT source. No upstream server, provider, DB or network is invoked.
"""
from pathlib import Path
import os,json,subprocess,hashlib,re,sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/weknora-port/evidence/SEQ_IMAGE_COMPLETION_20261015'
WORK=Path(r'E:\WK\run\seq-image-completion-20261015\go')
UP=Path(r'E:\RAG-reference\WeKnora-3e8b0bfc')
GO=Path(r'E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe')
def extract(text,prefix):
    start=text.index(prefix);end=text.index('\n}',start)+2
    return text[start:end]
src=(UP/'internal/searchutil/imageinfo.go').read_text(encoding='utf-8')
collector=extract(src,'func collectImageInfoByChunkIDs(')
enrich=extract(src,'func EnrichSearchResultsImageInfo(')
struct=extract((UP/'internal/types/chunk.go').read_text(encoding='utf-8'),'type ImageInfo struct {')
raw_functions={'collector':collector,'enrich':enrich,'image_info_type':struct}
# Only type package qualification changes; repository is a typed fixture adapter.
adapted=collector.replace('types.','')+'\n'+enrich.replace('types.','').replace('interfaces.ChunkRepository','fixtureRepo')
def img(url='a',**kw):return json.dumps([dict(url=url,**kw)],separators=(',',':'))
def child(id,parent='p',kind='image_ocr',raw=None,enabled=True):
    return dict(id=id,parent_chunk_id=parent,chunk_type=kind,image_info=img() if raw is None else raw,is_enabled=enabled)
def case(name,children,results=None,fail=0):
    return dict(name=name,children=children,results=results or [dict(id='p',image_info='')],fail_at=fail)
cases=[case('direct-six-fields',[child('a',raw=img(caption='caption',ocr_text='OCR',page_number=3,source_type='owner',ocr_layout={'x':1}))]),
 case('two-level',[child('t',kind='text',raw=''),child('g','t','image_caption',img(caption='second'))]),
 case('two-parents',[child('a'),child('t','q','text',''),child('b','t','image_caption',img('b',ocr_text='B'))],[dict(id='p',image_info=''),dict(id='q',image_info='')]),
 case('nonempty-literal',[child('a')],[dict(id='p',image_info='[]'),dict(id='q',image_info=' '),dict(id='r',image_info='bad')]),
 case('first-batch-failure',[child('a')],fail=1),
 case('second-batch-failure',[child('a'),child('t',kind='text',raw=''),child('g','t',raw=img('grandchild'))],fail=2),
 case('disabled-and-other-type',[child('a',enabled=False),child('b',kind='summary')]),
 case('whole-array-type-error',[child('a',raw='[{"url":"valid"},{"url":false}]')]),
 case('duplicate-update-text-only',[child('a',raw=img('a',original_url='original',start_pos=3,end_pos=9,ocr_text='first',caption='first')),child('b',raw=img('a',original_url='changed',start_pos=44,ocr_text='second',caption='')),child('c',raw=img('a',caption='last'))]),
 case('fallback-distinct-keys',[child('a',raw=json.dumps([{'original_url':'b','caption':'fallback'},{'url':'a','original_url':'b'},{'url':'b','caption':'replace fallback'}]))]),
 case('null-defaults',[child('a',raw='[null,{"url":"a","start_pos":null,"caption":null}]')]),
 case('no-url',[child('a',raw='[{"caption":"no URL"}]')]),
 case('invalid-int-whole-array',[child('a',raw='[{"url":"a","start_pos":1.5}]')]),
 case('nonarray',[child('a',raw='{"url":"a"}')]),
 case('disabled-text-blocks-grandchildren',[child('t',kind='text',raw='',enabled=False),child('g','t',raw=img('hidden'))]),
 case('duplicates-preserve-nonempty',[child('a')],[dict(id='p',image_info=''),dict(id='p',image_info=''),dict(id='p',image_info=' [] ')])]
main=r"""
package main
import("context";"encoding/json";"os";"errors")
type Chunk struct { ID string `json:"id"`; ParentChunkID string `json:"parent_chunk_id"`; ChunkType string `json:"chunk_type"`; ImageInfo string `json:"image_info"`; IsEnabled bool `json:"is_enabled"` }
const(ChunkTypeImageOCR="image_ocr";ChunkTypeImageCaption="image_caption";ChunkTypeText="text")
type SearchResult struct {ID string `json:"id"`; ImageInfo string `json:"image_info"`}
type Case struct {Name string `json:"name"`; Children []*Chunk `json:"children"`; Results []*SearchResult `json:"results"`; FailAt int `json:"fail_at"`}
type fixtureRepo struct {Rows []*Chunk; Calls *[][]string; FailAt int}
func(r fixtureRepo) list(ids []string)([]*Chunk,error){*r.Calls=append(*r.Calls,append([]string{},ids...));if len(*r.Calls)==r.FailAt{return nil,errors.New("fixture failure")};var rows []*Chunk;for _,row:=range r.Rows{for _,id:=range ids{if row.ParentChunkID==id{rows=append(rows,row);break}}};return rows,nil}
func CollectImageInfoByChunkIDs(ctx context.Context,r fixtureRepo,tenant uint64,ids []string)map[string]string{return collectImageInfoByChunkIDs(ids,r.list)}
func main(){var cases []Case;if err:=json.NewDecoder(os.Stdin).Decode(&cases);err!=nil{panic(err)};out:=[]any{};for _,c:=range cases{calls:=[][]string{};r:=fixtureRepo{c.Children,&calls,c.FailAt};EnrichSearchResultsImageInfo(context.Background(),r,1,c.Results);out=append(out,map[string]any{"name":c.Name,"results":c.Results,"calls":calls})};json.NewEncoder(os.Stdout).Encode(out)}
"""
program=main+'\n'+struct+'\n'+adapted+'\n'
OUT.mkdir(exist_ok=True);WORK.mkdir(parents=True,exist_ok=True)
assert not (OUT/'go.exitcode.txt').exists(),'REFUSE_EVIDENCE_OVERWRITE'
(OUT/'oracle.go').write_text('// Source: Tencent/WeKnora@3e8b0bfc (MIT). Extracted bodies with type/repository adapters.\n'+program,encoding='utf-8')
file=WORK/'oracle.go';file.write_text(program,encoding='utf-8')
env=dict(os.environ)
for k,sub in [('GOCACHE','gocache'),('GOTMPDIR','tmp'),('GOPATH','gopath'),('GOMODCACHE','modcache'),('TEST_TELEMETRY_DIR','telemetry')]:
    p=WORK/sub;p.mkdir(exist_ok=True);env[k]=str(p)
env['TMP']=env['TEMP']=str(WORK/'tmp')
env.update(GOENV='off',GO111MODULE='off',GOTOOLCHAIN='local',GOPROXY='off',GOSUMDB='off',CGO_ENABLED='0')
(WORK/'telemetry/mode').write_text('off',encoding='utf-8')
cmd=[str(GO),'run',str(file)]
(OUT/'go.command.json').write_text(json.dumps({'argv':cmd,'cwd':str(WORK),'env':{k:env[k] for k in ['GOCACHE','GOTMPDIR','GOPATH','GOMODCACHE','TEST_TELEMETRY_DIR','TEMP','TMP','GOENV','GO111MODULE','GOTOOLCHAIN','GOPROXY','GOSUMDB','CGO_ENABLED']}},indent=2)+'\n',encoding='utf-8')
p=subprocess.run(cmd,cwd=WORK,env=env,input=json.dumps(cases).encode('utf-8'),capture_output=True)
(OUT/'go.stdout.txt').write_bytes(p.stdout);(OUT/'go.stderr.txt').write_bytes(p.stderr);(OUT/'go.exitcode.txt').write_text(str(p.returncode)+'\n',encoding='utf-8')
if p.returncode:print(p.stderr.decode('utf-8',errors='replace'));sys.exit(p.returncode)
rows=json.loads(p.stdout)
fixture={'upstream_commit':'3e8b0bfc80b845b2d4b2ed683994748741450a97','scope':'SOURCE_EXTRACTED_TYPED_REPOSITORY_ADAPTER; not full upstream application','exit_code':p.returncode,'cases':cases,'rows':rows,'extracted_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in raw_functions.items()},'adapted_bodies_sha256':hashlib.sha256(adapted.encode()).hexdigest()}
(ROOT/'tests/weknora_port/fixtures/image_completion_go_oracle.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('GO_SOURCE_COLLECTOR_ENRICH_CASES',len(rows),'EXIT',p.returncode)
