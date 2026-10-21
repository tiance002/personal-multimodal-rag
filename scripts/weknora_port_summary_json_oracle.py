"""Execute pinned source summary JSON recovery in a stdlib-only Go harness."""
import hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path('E:/RAG-reference/WeKnora-3e8b0bfc');PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
GO='E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe'
assert subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()==PIN

def source(path):return subprocess.check_output(['git','-C',str(ROOT),'show','HEAD:'+path]).decode()
def function(s,name):
    a=s.index('func '+name+'(');b=s.index('\n}'+ '\n',a)+2;return s[a:b]
process=source('internal/application/service/knowledge_process.go');common=source('internal/common/tools.go')
profile=source('internal/types/knowledge_profile.go')
parse=function(process,'parseDocumentSummaryOutput').replace('common.ParseLLMJsonResponse','ParseLLMJsonResponse').replace('types.KnowledgeProfile','KnowledgeProfile')
helper=function(common,'ParseLLMJsonResponse')+'\n'+function(common,'ExtractBalancedJSON')
a=process.index('type documentProfileOutput');b=process.index('\n}',a)+2
struct=process[a:b]
work=Path('var/weknora-port/oracle/summary-json');work.mkdir(parents=True,exist_ok=True)
(work/'profile.go').write_text(profile,encoding='utf-8')
(work/'go.mod').write_text('module summary_oracle\n\ngo 1.26.0\n')
(work/'summary.go').write_text('package types\nimport("encoding/json";"regexp";"strings")\n'+
    next(line for line in common.splitlines() if line.startswith('var jsonCodeFenceRE'))+'\n'+
    'type documentSummaryResult struct {Summary string;Profile *KnowledgeProfile}\n'+struct+'\n'+helper+'\n'+parse,encoding='utf-8')
cases=[
 ' Legacy plain ', '{"summary":"x","gist":" y ","topics":["k","K."]}',
 'Sure, here is the profile: {"summary":"brief","gist":"topic","topics":["A"]} trailing [1]',
 'Before ```json\n{"gist":"fenced"}\n``` after',
 'Before [1] {"summary":"later object must not win"}',
 '{"summary":"raw\nnewline"}', '{"summary":"escaped\\nnewline"}',
 '{"SUMMARY":"case folded","topics":[null,"x"]}', '{"summary":123}', '{"topics":[]}',
 '{"summary":"braces } [ within string"}', 'null', '[{"summary":"array wrong target"}]']
program='package types\nimport("encoding/json";"fmt";"testing")\nfunc TestEmit(t *testing.T){var cases []string;if e:=json.Unmarshal([]byte(CASES),&cases);e!=nil{t.Fatal(e)}\nfor i,v:=range cases{r:=parseDocumentSummaryOutput(v);b,_:=json.Marshal(map[string]any{"index":i,"summary":r.Summary,"profile":r.Profile});fmt.Println("ORACLE "+string(b))}}\n'.replace('CASES',json.dumps(json.dumps(cases,ensure_ascii=False),ensure_ascii=False))
(work/'summary_test.go').write_text(program,encoding='utf-8')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOMODCACHE=str(Path('var/weknora-port/oracle/go-mod-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run([GO,'test','-v','.'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
log=r.stdout.decode(errors='replace');Path('docs/weknora-port/evidence/P2_SUMMARY_JSON_GO.stdout.txt').write_text(log,encoding='utf-8')
Path('docs/weknora-port/evidence/P2_SUMMARY_JSON_GO.exitcode.txt').write_text(str(r.returncode)+'\n')
rows=[json.loads(line[7:]) for line in log.splitlines() if line.startswith('ORACLE ')]
fixture={'upstream':PIN,'scope':'source parseDocumentSummaryOutput + common JSON recovery + KnowledgeProfile.Normalize; namespace-only adapters, not full worker',
 'source_function_sha256':hashlib.sha256(function(process,'parseDocumentSummaryOutput').encode()).hexdigest(),
 'cases':cases,'rows':rows,'exit_code':r.returncode,'provider_calls':0}
Path('tests/weknora_port/fixtures/summary_json_go_oracle.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':r.returncode,'rows':len(rows),'provider_calls':0}));raise SystemExit(r.returncode)
