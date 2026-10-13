"""Execute fixed-source KnowledgeProfile and sampleLongContent, no providers."""
import hashlib,json,subprocess
from pathlib import Path
PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
ROOT=Path('E:/RAG-reference/WeKnora-3e8b0bfc')
GO=Path('E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe')
def source(path):return subprocess.check_output(['git','-C',str(ROOT),'show','HEAD:'+path])
assert subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()==PIN
profile=source('internal/types/knowledge_profile.go')
process=source('internal/application/service/knowledge_process.go').decode()
start=process.index('func sampleLongContent(');end=process.index('\n// ProcessSummaryGeneration',start)
sample=process[start:end]
work=Path('var/weknora-port/oracle/document-profile');work.mkdir(parents=True,exist_ok=True)
(work/'profile.go').write_bytes(profile)
(work/'sample.go').write_text('package types\n\n'+sample,encoding='utf-8')
(work/'go.mod').write_text('module profile_oracle\n\ngo 1.26.0\n')
cases=[{'gist':'  hello\n world  ','topics':['Kubernetes ','kubernetes.','!K8s!','$$$','中文，','中文','topic5','topic6'], 'doc_type':' '*3,'typical_question':'Q?'},
 {'gist':'中'*130,'topics':['x'*50,'x'*40,'A','a','B','C','D','E']}, {}]
limits=[60,150,151,200,8192]
program='''package types
import("encoding/json";"fmt";"strings";"testing")
func TestEmit(t *testing.T){
raw:=`CASES`;var cases []KnowledgeProfile;if err:=json.Unmarshal([]byte(raw),&cases);err!=nil {t.Fatal(err)}
for i,p:=range cases {b,_:=json.Marshal(map[string]any{"kind":"profile","index":i,"output":p.Normalize()});fmt.Println("ORACLE "+string(b))}
content:=strings.Repeat("甲乙😊abc ",2000)
for _,n:=range []int{60,150,151,200,8192} {b,_:=json.Marshal(map[string]any{"kind":"sample","limit":n,"output":sampleLongContent(content,n)});fmt.Println("ORACLE "+string(b))}}
'''.replace('CASES',json.dumps(cases,ensure_ascii=False))
(work/'profile_test.go').write_text(program,encoding='utf-8')
result=subprocess.run([str(GO),'test','-v','.'],cwd=work,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
log=result.stdout.decode(errors='replace');(work/'execution.log').write_text(log,encoding='utf-8')
rows=[json.loads(line[7:]) for line in log.splitlines() if line.startswith('ORACLE ')]
output={'pin':PIN,'scope':'UNMODIFIED_KNOWLEDGE_PROFILE_FILE_AND_SAMPLE_LONG_CONTENT_FUNCTION_ONLY',
 'exit_code':result.returncode,'provider_calls':0,'source_sha256':hashlib.sha256(profile).hexdigest(),
 'sample_body_sha256':hashlib.sha256(sample.encode()).hexdigest(),'profile_inputs':cases,
 'sample_input':'甲乙😊abc '*2000,'rows':rows}
Path('tests/weknora_port/fixtures/document_profile_go_oracle.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':result.returncode,'rows':len(rows),'provider_calls':0,'scope':output['scope']}))
raise SystemExit(result.returncode)
