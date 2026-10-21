"""Pinned source image prompt/enrichment/plain-markdown OCR function oracle."""
import json,os,subprocess
from pathlib import Path
ROOT=Path('E:/RAG-reference/WeKnora-3e8b0bfc');PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
assert subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()==PIN
def source(path):return subprocess.check_output(['git','-C',str(ROOT),'show','HEAD:'+path]).decode()
def fn(s,name):
 a=s.index('func '+name+'(');return s[a:s.index('\n}\n',a)+2]
mult=source('internal/application/service/image_multimodal.go');ocr=source('internal/application/service/ocr_sanitizer.go');images=source('internal/searchutil/imageinfo.go')
types=source('internal/types/chunk.go');instructions=source('internal/types/prompt_instructions.go');context=source('internal/types/context_helpers.go')
const=mult[mult.index('const ('):mult.index('\n)',mult.index('const ('))+2];variables=ocr[ocr.index('var ('):ocr.index('\n)',ocr.index('var ('))+2]
a=types.index('type ImageInfo struct');info=types[a:types.index('\n}',a)+2]
functions='\n'.join(fn(mult,n) for n in ('buildVLMOCRPrompt','buildVLMCaptionPrompt'))+'\n'+fn(instructions,'AppendCustomPromptInstructions')+'\n'+fn(context,'LanguageLocaleName')
functions+='\n'+'\n'.join(fn(images,n) for n in ('EnrichContentCaptionOnly','EnrichContentCaptionAndOCR','buildCaptionOCRBlock'))
functions+='\n'+'\n'.join(fn(ocr,n) for n in ('sanitizeOCRText','stripMarkdownCodeBlock','looksLikeHTML','isKnownEmptyReply'))
functions=functions.replace('types.','').replace('docparser.NormalizeHTMLTables','normalizeHTMLTablesNoop')
body='package oracle\nimport("context";"encoding/json";"fmt";"regexp";"strings")\n'+const+'\n'+variables+'\n'+info+'\n'+functions
body+='\ntype VLMConfig struct {DescriptionLanguage string;CustomInstructions string}\nfunc LanguageNameFromContext(ctx context.Context) string {return LanguageLocaleName(ctx.Value("fixture_locale").(string))}\n'
body+='func ocrHTMLToMarkdown(s string) string {panic("HTML conversion outside oracle scope")}\nfunc normalizeHTMLTablesNoop(s string) string {if strings.Contains(strings.ToLower(s),"<table"){panic("HTML tables outside oracle scope")};return s}\n'
body+=next(line for line in images.splitlines() if line.startswith('var MarkdownImageRegex'))+'\n'
work=Path('E:/WK/oracle/ocr-sparse');work.mkdir(parents=True,exist_ok=True)
(work/'go.mod').write_text('module image_oracle\n\ngo 1.26.0\n');(work/'image.go').write_text(body,encoding='utf-8')
cases=['This is mostly text with a <b>bold</b> word.', '这是一段很长的普通文字，带有一个<b>强调内容</b>，其余文字保持原样。', 'Normal text with a <span>detail</span> followed by an extended paragraph to preserve the original formatting.', '<html><img/></html>']
infos=[{'url':'image.png','original_url':'image.png','caption':'A sheet','ocr_text':'Extracted words'}, {'url':'unmatched.png','original_url':'unmatched.png','caption':'Extra','ocr_text':'Other'}]
program='package oracle\nimport("context";"encoding/json";"fmt";"testing")\nfunc TestEmit(t *testing.T){var cases []string;if e:=json.Unmarshal([]byte(CASES),&cases);e!=nil{t.Fatal(e)}\nfor i,v:=range cases{b,_:=json.Marshal(map[string]any{"kind":"ocr","index":i,"output":sanitizeOCRText(v)});fmt.Println("ORACLE "+string(b))}\nctx:=context.WithValue(context.Background(),"fixture_locale","zh-CN");cfg:=VLMConfig{CustomInstructions:" CAPTION ONLY "}\nfor _,source:=range []string{"scanned_pdf","standalone_image"}{b,_:=json.Marshal(map[string]any{"kind":"ocr_prompt","source":source,"output":buildVLMOCRPrompt(source,cfg)});fmt.Println("ORACLE "+string(b))}\nb,_:=json.Marshal(map[string]any{"kind":"caption","output":buildVLMCaptionPrompt(ctx,cfg)});fmt.Println("ORACLE "+string(b))\nraw:=INFOS;content:="body ![alt](image.png)"\nfor _,full:=range []bool{false,true}{value:=EnrichContentCaptionOnly(content,raw);if full{value=EnrichContentCaptionAndOCR(content,raw)};b,_:=json.Marshal(map[string]any{"kind":"enrich","full":full,"output":value});fmt.Println("ORACLE "+string(b))}}\n'.replace('CASES',json.dumps(json.dumps(cases,ensure_ascii=False),ensure_ascii=False)).replace('INFOS',json.dumps(json.dumps(infos,ensure_ascii=False),ensure_ascii=False))
(work/'image_test.go').write_text(program,encoding='utf-8')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','test','-v','.'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
log=r.stdout.decode(errors='replace');Path('docs/weknora-port/evidence/P2_OCR_SPARSE_GO.stdout.txt').write_text(log,encoding='utf-8');Path('docs/weknora-port/evidence/P2_OCR_SPARSE_GO.exitcode.txt').write_text(str(r.returncode)+'\n')
rows=[json.loads(line[7:]) for line in log.splitlines() if line.startswith('ORACLE ')]
value={'upstream':PIN,'scope':'source looksLikeHTML/sanitizeOCRText sparse inline tags; full HTML/table converter still excluded','cases':cases,'infos':infos,'rows':rows,'exit_code':r.returncode,'provider_calls':0}
Path('tests/weknora_port/fixtures/ocr_sparse_go_oracle.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':r.returncode,'rows':len(rows),'provider_calls':0}));raise SystemExit(r.returncode)
