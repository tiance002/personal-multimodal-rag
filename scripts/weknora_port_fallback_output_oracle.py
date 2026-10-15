"""Fixed Go fallback consumer/render functions; explicit plain-text decoder/DTO seams."""
from pathlib import Path
import subprocess,os,hashlib,json
GIT='C:/Program Files/Git/cmd/git.exe';ROOT='E:/RAG-reference/WeKnora-3e8b0bfc';PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
assert subprocess.check_output([GIT,'-C',ROOT,'rev-parse','HEAD']).decode().strip()==PIN
source=subprocess.check_output([GIT,'-C',ROOT,'show',PIN+':internal/application/service/session_knowledge_qa.go']).decode('utf-8')
handler=subprocess.check_output([GIT,'-C',ROOT,'show',PIN+':internal/handler/session/qa.go']).decode('utf-8')
def fn(prefix):
    a=source.index(prefix);return source[a:source.index('\n}',a)+2]
functions=[fn('func (s *sessionService) consumeFallbackStream('),fn('func (s *sessionService) emitFallbackAnswer('),fn('func (s *sessionService) handleFixedFallback('),fn('func (s *sessionService) renderFallbackPrompt(')]
append_line=next(line.strip() for line in handler.splitlines() if 'streamCtx.assistantMessage.Content += data.Content' in line)
code=r'''package main
import("context";"strings";"fmt";"encoding/json")
type EventType string
const EventAgentFinalAnswer="answer"
const ResponseTypeAnswer="answer"
const EmptyTruncatedAnswerFallback="length-empty-seam-not-used-in-inputs"
type ChatResponse struct{Content string}
type StreamResponse struct{ResponseType string;Content string;Done bool;FinishReason string}
type AgentFinalAnswerData struct{Content string `json:"content"`;Done bool `json:"done"`;IsFallback bool `json:"is_fallback"`;Truncated bool `json:"truncated"`}
type Event struct{ID string;Type EventType;SessionID string;Data any}
type Bus struct{Events []AgentFinalAnswerData;Content string;Completed int}
func(b *Bus)Emit(_ context.Context,e Event)error{data:=e.Data.(AgentFinalAnswerData);b.Events=append(b.Events,data);APPEND_LINE;if data.Done{b.Completed++};return nil}
type Registry struct{}
type Decoder struct{}
func(*Registry)StreamDecoder()*Decoder{return &Decoder{}}
func(*Decoder)Feed(s string)string{return s}
func(*Decoder)Flush()string{return ""}
func(*Registry)DecodeOutputText(s string)string{return s}
func NewRegistry(bool)*Registry{return &Registry{}}
func IsLengthFinishReason(s string)bool{return s=="length"}
type ChatManage struct{EventBus *Bus;ChatResponse *ChatResponse;SessionID string;FallbackResponse string;Query string;RewriteQuery string;Language string;FallbackPrompt string;ImageDescription string;ChatModelSupportsVision bool;QuotedContext string;Documents string}
func(*ChatManage)CitationsEnabled()bool{return true}
type sessionService struct{}
func(*sessionService)buildKBDocumentListing(_ context.Context,c *ChatManage)string{return c.Documents}
type PlaceholderValues map[string]string
func RenderPromptPlaceholders(s string,v PlaceholderValues)string{for k,x:=range v{s=strings.ReplaceAll(s,"{{"+k+"}}",x)};return s}
type Logger struct{}
func(Logger)Errorf(context.Context,string,...any){}
func(Logger)Warnf(context.Context,string,...any){}
func(Logger)Infof(context.Context,string,...any){}
var logger Logger
var counter int
func generateEventID(s string)string{counter++;return fmt.Sprintf("%s-%d",s,counter)}
'''.replace('APPEND_LINE',append_line.replace('streamCtx.assistantMessage.Content','b.Content'))
for function in functions:
    for prefix in ('types.','event.','chatpipeline.','modelcontext.'):function=function.replace(prefix,'')
    code+=function+'\n'
code+=r'''
func main(){
  cases:=[]struct{Name string;Rows []StreamResponse}{
    {"empty",nil},{"partial_eof",[]StreamResponse{{ResponseType:"answer",Content:"partial "}}},
    {"normal_done",[]StreamResponse{{ResponseType:"answer",Content:"normal answer",Done:true,FinishReason:"stop"}}},
    {"thinking_ignored",[]StreamResponse{{ResponseType:"thinking",Content:"private thought"},{ResponseType:"answer",Content:"partial "}}},
  }
  for _,c:=range cases{bus:=&Bus{};cm:=&ChatManage{EventBus:bus,FallbackResponse:"FIXED_SOURCE_REPLY"};ch:=make(chan StreamResponse,len(c.Rows));for _,row:=range c.Rows{ch<-row};close(ch);(&sessionService{}).consumeFallbackStream(context.Background(),cm,ch,NewRegistry(true));b,_:=json.Marshal(map[string]any{"name":c.Name,"events":bus.Events,"persisted_content":bus.Content,"completion_count":bus.Completed});fmt.Println(string(b))}
  cm:=&ChatManage{Query:"q0",RewriteQuery:" rewritten question ",Language:"English",FallbackPrompt:"FALLBACK {{query}} {{language}} {{kb_documents}}",ImageDescription:"a public widget picture",QuotedContext:"QUOTED SOURCE",Documents:"- doc\n"}
  // Literal newline, matching the Python fixture.
  cm.Documents="- doc\n";cm.Documents=strings.ReplaceAll(cm.Documents,"\\n","\n")
  prompt,_:=(&sessionService{}).renderFallbackPrompt(context.Background(),cm);b,_:=json.Marshal(map[string]any{"name":"nonvision_image","prompt":prompt});fmt.Println(string(b))
}
'''
work=Path('E:/WK/oracle/fallback-output-20261015');work.mkdir(parents=True,exist_ok=True)
(work/'main.go').write_text(code,encoding='utf-8');(work/'go.mod').write_text('module fallbackoutputoracle\n\ngo 1.26.0\n',encoding='utf-8')
env=dict(os.environ,GOCACHE='E:/WK/go/cache',GOMODCACHE='D:/WK/go/modules',GOTMPDIR='E:/WK/go/tmp',GOTOOLCHAIN='local',GOPROXY='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','run','.'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
output=Path('docs/weknora-port/evidence/QA_FALLBACK_OUTPUT_20261015')
(output/'go.stdout.txt').write_bytes(r.stdout);(output/'go.stderr.txt').write_bytes(r.stderr);(output/'go.exitcode.txt').write_text(str(r.returncode)+'\n')
rows=[json.loads(line) for line in r.stdout.decode().splitlines() if line.startswith('{')]
value={'upstream':PIN,'exit_code':r.returncode,'rows':rows,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
    'handler_sha256':hashlib.sha256(handler.encode()).hexdigest(),'handler_append_statement':append_line,
    'function_sha256':{f.split('(')[0]+'-'+str(i):hashlib.sha256(f.encode()).hexdigest() for i,f in enumerate(functions)},
    'scope':'Exact fixed fallback consumer/emitter/fixed/render functions plus exact handler append statement. DTO/event recorder, identity decoder for plain text only, listing and deterministic placeholders, unused truncation helper seams; not public KnowledgeQA, citation decoding or actual Go DB.',
    'real_provider_calls':0}
Path('tests/weknora_port/fixtures/fallback_output_go_20261015.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':r.returncode,'rows':len(rows)}));raise SystemExit(r.returncode)
