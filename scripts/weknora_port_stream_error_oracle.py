"""Execute fixed Go stream consumer/resource decoder with minimal DTO/event seams."""
import hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path('E:/RAG-reference/WeKnora-3e8b0bfc');PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
assert subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip()==PIN
def src(name):return subprocess.check_output(['git','-C',str(ROOT),'show',PIN+':'+name]).decode('utf-8')
loop=src('internal/application/service/chat_pipeline/chat_completion_stream.go')
stream=src('internal/modelcontext/stream.go')
a=loop.index('go func() {')+len('go func() {');b=loop.index('\n\t}()',a)
body=loop[a:b].replace('types.','').replace('event.','').replace('uuid.New().String()','nextID()')
resource=stream[stream.index('type streamHold struct'):stream.index('// HandleStreamDecoder')]
constants=loop[loop.index('const EmptyTruncatedAnswerFallback'):loop.index('// PluginChatCompletionStream')]
code='package main\nimport("context";"fmt";"strings";"encoding/json";"os")\n'+constants+'\n'+resource
code+=r"""
type resourceRegistry struct{}
func (*resourceRegistry) handles() []string {return []string{"res://0001"}}
func (*resourceRegistry) DecodeText(s string) string {return strings.ReplaceAll(s,"res://0001","resource://abcdefghijklmnopqrstuv")}
type ModelContext struct{}
func (*ModelContext) StreamDecoder() *resourceStreamDecoder {return newResourceStreamDecoder(&resourceRegistry{})}
type EventType string
const (EventAgentThought="thinking";EventAgentFinalAnswer="answer";EventError="error";ResponseTypeError="error";ResponseTypeThinking="thinking";ResponseTypeAnswer="answer")
type AgentThoughtData struct {Content string;Done bool}
type AgentFinalAnswerData struct {Content string;Done bool;Truncated bool}
type ErrorData struct {Error string;Stage string;SessionID string}
type Event struct {ID string;Type EventType;SessionID string;Data any}
type Bus struct {Rows []map[string]any}
func(b *Bus) Emit(ctx context.Context,e Event) error {row:=map[string]any{"kind":string(e.Type),"content":"","done":false};switch d:=e.Data.(type){case AgentThoughtData:row["content"]=d.Content;row["done"]=d.Done;case AgentFinalAnswerData:row["content"]=d.Content;row["done"]=d.Done;case ErrorData:row["content"]=d.Error;row["done"]=true};b.Rows=append(b.Rows,row);return nil}
func nextID() string {return "fixture-id"}
func pipelineInfo(context.Context,string,string,map[string]interface{}){}
func pipelineError(context.Context,string,string,map[string]interface{}){}
type Manage struct {SessionID string}
type Response struct {ResponseType string;Content string;Done bool;FinishReason string}
func consume(ctx context.Context,responseChan <-chan Response,eventBus *Bus,chatManage *Manage,modelContext *ModelContext){
"""+body+'\n}\n'
code+=r"""
func main(){var cases [][]Response;if err:=json.NewDecoder(os.Stdin).Decode(&cases);err!=nil{panic(err)};out:=[][]map[string]any{};for _,rows:=range cases{ch:=make(chan Response,len(rows));for _,r:=range rows{ch<-r};close(ch);bus:=&Bus{};consume(context.Background(),ch,bus,&Manage{SessionID:"session"},&ModelContext{});out=append(out,bus.Rows)};json.NewEncoder(os.Stdout).Encode(out)}
"""
cases=[[
 {'ResponseType':'thinking','Content':'first','Done':True},
 {'ResponseType':'thinking','Content':'second'},
 {'ResponseType':'answer','Content':'partial '},
 {'ResponseType':'error','Content':'PROVIDER_RESPONSE_INVALID'},
 {'ResponseType':'answer','Content':'recovered','Done':True,'FinishReason':'stop'}],
 [{'ResponseType':'thinking','Content':'thought re'},{'ResponseType':'answer','Content':'answer re'},{'ResponseType':'error','Content':'PROVIDER_RESPONSE_INVALID'}],
 [{'ResponseType':'answer','Content':'image re'},{'ResponseType':'error','Content':'PROVIDER_RESPONSE_INVALID'},{'ResponseType':'answer','Content':'s://0001','Done':True,'FinishReason':'stop'}]]
work=Path('E:/WK/oracle/stream-error');work.mkdir(parents=True,exist_ok=True)
(work/'main.go').write_text(code,encoding='utf-8');(work/'go.mod').write_text('module streamoracle\n\ngo 1.26.0\n')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','run','.'],cwd=work,env=env,input=json.dumps(cases).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
Path('docs/weknora-port/evidence/P1_STREAM_GO_20261014.stderr.txt').write_bytes(r.stderr)
value={'upstream':PIN,'scope':'exact OnEvent channel-consumer goroutine and exact resource streamHold/decoder; stubbed DTO/event bus/registry. Not full Go engine/citation oracle.','source_sha256':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in [('chat_completion_stream.go',loop),('stream.go',stream)]},'cases':cases,'rows':json.loads(r.stdout) if r.returncode==0 else [],'exit_code':r.returncode,'real_provider_calls':0}
Path('tests/weknora_port/fixtures/stream_error_go_oracle.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':r.returncode,'cases':len(value['rows'])}));raise SystemExit(r.returncode)
