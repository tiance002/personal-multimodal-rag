"""Exact source ConfigFromModel/NewChat dispatch with constructor seams, no network."""
import hashlib,json,subprocess,os
from pathlib import Path
pin='3e8b0bfc80b845b2d4b2ed683994748741450a97';repo='E:/RAG-reference/WeKnora-3e8b0bfc'
assert subprocess.check_output(['git','-C',repo,'rev-parse','HEAD']).decode().strip()==pin
raw=subprocess.check_output(['git','-C',repo,'show',pin+':internal/models/chat/chat.go']);text=raw.decode()
def block(prefix):
 a=text.index(prefix);return text[a:text.index('\n}',a)+2]
code='package main\nimport("strings";"fmt";"encoding/json";"os")\n'
code+=block('type ChatConfig struct {').replace('types.','')+'\n'
code+='type ModelSource string\ntype ModelSpecOverride struct{}\ntype Params struct{APIKey,BaseURL,Provider string;MaxConcurrency int;ExtraConfig,CustomHeaders map[string]string;Spec *ModelSpecOverride}\ntype Model struct{ID,Name string;Source ModelSource;Parameters Params}\ntype OllamaService struct{}\ntype FakeChat struct{Dispatch,Provider string}\ntype Chat interface{}\nconst ModelSourceLocal="local"\nconst ModelSourceRemote="remote"\n'
code+=block('func ConfigFromModel(').replace('types.','')+'\n'
code+=block('func NewChat(').replace('types.','').replace('ollama.OllamaService','OllamaService')+'\n'
code+='func NewOllamaChat(c *ChatConfig,s *OllamaService)(Chat,error){return FakeChat{"local",c.Provider},nil}\nfunc NewRemoteChat(c *ChatConfig)(Chat,error){return FakeChat{"remote",c.Provider},nil}\nfunc wrapChatDebug(c Chat,e error)(Chat,error){return c,e}\nfunc wrapChatLangfuse(c Chat,e error)(Chat,error){return c,e}\nfunc wrapChatConcurrency(c Chat,m int,e error)(Chat,error){return c,e}\n'
code+='func main(){var cases []*Model;json.NewDecoder(os.Stdin).Decode(&cases);rows:=[]any{};for _,m:=range cases{c:=ConfigFromModel(m,"","");if c==nil{rows=append(rows,nil);continue};v,e:=NewChat(c,&OllamaService{});err:="";if e!=nil{err=e.Error()};rows=append(rows,map[string]any{"source":c.Source,"provider":c.Provider,"model":c.ModelName,"dispatch":fmt.Sprintf("%v",v),"error":err})};json.NewEncoder(os.Stdout).Encode(rows)}'
cases=[{'ID':'remote','Name':'fixture','Source':'remote','Parameters':{'Provider':'deepseek','BaseURL':'http://fixture.invalid/v1'}},{'ID':'local','Name':'fixture','Source':'local','Parameters':{'Provider':'deepseek','BaseURL':'http://fixture.invalid/ignored'}},{'ID':'legacy','Name':'fixture','Source':'deepseek','Parameters':{'Provider':'deepseek'}},None]
work=Path('E:/WK/oracle/source-provider');work.mkdir(parents=True,exist_ok=True);(work/'go.mod').write_text('module sourceprovider\n\ngo 1.26.0\n',encoding='utf-8');(work/'main.go').write_text(code,encoding='utf-8')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','run','.'],cwd=work,env=env,input=json.dumps(cases).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
Path('docs/weknora-port/evidence/P1_SOURCE_PROVIDER_GO_RETRY_20261014.stderr.txt').write_bytes(r.stderr)
value={'upstream':pin,'source_sha256':hashlib.sha256(raw).hexdigest(),'cases':cases,'rows':json.loads(r.stdout) if r.returncode==0 else [],'exit_code':r.returncode,'scope':'Exact ConfigFromModel/NewChat; protocol constructors and wrappers stubbed. Not local/remote wire or full ordinary engine.'}
Path('tests/weknora_port/fixtures/source_provider_go_oracle.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps({'exit_code':r.returncode,'cases':len(value['rows'])}));raise SystemExit(r.returncode)