"""Exact fixed config/locale/reasoning/intent functions; minimal DTO seams."""
import json,os,subprocess,re,hashlib
from pathlib import Path
ROOT='E:/RAG-reference/WeKnora-3e8b0bfc';PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
assert subprocess.check_output(['git','-C',ROOT,'rev-parse','HEAD']).decode().strip()==PIN
def src(n):return subprocess.check_output(['git','-C',ROOT,'show',PIN+':'+n]).decode()
def fn(s,p):
 a=s.index(p);return s[a:s.index('\n}',a)+2]
conf=src('internal/config/config.go');q=src('internal/application/service/chat_pipeline/query_understand.go');lang=src('internal/types/context_helpers.go');middleware=src('internal/middleware/language.go');api=src('internal/models/api/types.go');h=src('internal/application/service/session_qa_helpers.go')
loader=fn(conf,'func backfillConversationDefaults(');find=fn(conf,'func FindTemplateByID(')
fields=sorted(set(re.findall(r'conv\.(\w+)',loader))-{'Summary','IntentSystemPrompts'})
collections=re.findall(r'pt\.(\w+),',find)
code='package main\nimport("context";"strings";"fmt";"encoding/json")\n'
code+='type PromptTemplate struct{ID string;Content string;User string}\ntype PromptTemplatesConfig struct{'+ ';'.join(x+' []PromptTemplate' for x in collections)+'}\n'
code+='type Summary struct{PromptID string;ContextTemplateID string;Prompt string;ContextTemplate string}\ntype Conversation struct{'+ ';'.join(x+' string' for x in fields)+';Summary *Summary;IntentSystemPrompts map[string]string}\ntype Config struct{PromptTemplates *PromptTemplatesConfig;Conversation *Conversation}\n'
code+=loader+'\n'+find+'\n'+fn(lang,'func LanguageLocaleName(')+'\n'+fn(middleware,'func parseFirstLanguageTag(')+'\n'
a=api.index('type ReasoningEffort string');b=api.index('// Graded reports',a);code+=api[a:b]+'\n'+fn(h,'func applyRequestReasoningEffort(').replace('api.','')+'\n'
code+=r"""
type Chat struct{ID string}
type ModelService struct{IDs map[string]bool;Calls []string}
func(s *ModelService)GetChatModel(ctx context.Context,id string)(*Chat,error){s.Calls=append(s.Calls,id);if !s.IDs[id]{return nil,fmt.Errorf("missing")};return &Chat{ID:id},nil}
type History struct{Query string;Answer string}
type Attachments []string
func(a Attachments)BuildPrompt()string{return strings.Join(a,"")}
type ChatManage struct{SessionID string;ChatModelID string;QueryUnderstandModelID string;VLMModelID string;ChatModelSupportsVision bool;RewritePromptUser string;RewritePromptSystem string;Query string;Language string;Images []string;Attachments Attachments;Intent string;IntentPromptOverrides map[string]string;SystemPromptOverride string;Memory string}
type PluginQueryUnderstand struct{modelService *ModelService;config *Config}
func(p *PluginQueryUnderstand)memoryBackground(ctx context.Context,c *ChatManage)string{return c.Memory}
type PlaceholderValues map[string]string
func RenderPromptPlaceholders(s string,v PlaceholderValues)string{for k,x:=range v{s=strings.ReplaceAll(s,"{{"+k+"}}",x)};return s}
func pipelineWarn(context.Context,string,string,map[string]interface{}){}
func pipelineError(context.Context,string,string,map[string]interface{}){}
"""
for prefix in ['func (p *PluginQueryUnderstand) selectModel(','func (p *PluginQueryUnderstand) buildPrompts(','func applyIntentPromptOverride(','func formatConversationHistory(']:code+=fn(q,prefix).replace('types.','').replace('chat.Chat','*Chat')+'\n'
code+=r"""
func emit(k string,i any,o any){b,_:=json.Marshal(map[string]any{"kind":k,"input":i,"output":o});fmt.Println("ORACLE "+string(b))}
func main(){
pt:=&PromptTemplatesConfig{SystemPrompt:[]PromptTemplate{{ID:"same",Content:"SYSTEM FIRST"},{ID:"sys",Content:"FINAL {{language}}"}},Rewrite:[]PromptTemplate{{ID:"same",Content:"SHOULD NOT WIN"},{ID:"rw",Content:"REWRITE {{language}} {{conversation}}",User:"USER {{query}}"}},ContextTemplate:[]PromptTemplate{{ID:"ctx",Content:"CONTEXT {{query}}"}},IntentPrompts:[]PromptTemplate{{ID:"greeting",Content:"GLOBAL {{language}}"},{ID:"",Content:"skip"},{ID:"empty",Content:""}}}
pt.GenerateSummary=[]PromptTemplate{{ID:"known",Content:"RESOLVED SUMMARY"}};pt.Fallback=[]PromptTemplate{{ID:"known-fallback",Content:"RESOLVED FALLBACK"}}
for _,id:=range []string{"known","missing",""}{c:=&Conversation{GenerateSummaryPromptID:id,GenerateSummaryPrompt:"RAW YAML",Summary:&Summary{}};backfillConversationDefaults(&Config{Conversation:c,PromptTemplates:pt});emit("qa_summary",id,c.GenerateSummaryPrompt)}
for _,id:=range []string{"known-fallback","missing",""}{c:=&Conversation{FallbackPromptID:id,FallbackPrompt:"RAW FALLBACK",Summary:&Summary{}};backfillConversationDefaults(&Config{Conversation:c,PromptTemplates:pt});emit("qa_fallback",id,c.FallbackPrompt)}
for _,id:=range []string{"rw","missing","same"}{c:=&Conversation{RewritePromptID:id,RewritePromptSystem:"SAVED",RewritePromptUser:"USER SAVED",Summary:&Summary{PromptID:"sys",ContextTemplateID:"ctx"}};backfillConversationDefaults(&Config{Conversation:c,PromptTemplates:pt});emit("startup",id,map[string]any{"system":c.RewritePromptSystem,"user":c.RewritePromptUser,"prompt":c.Summary.Prompt,"context":c.Summary.ContextTemplate,"intents":c.IntentSystemPrompts})}
for _,v:=range []string{"off","none","on","high","HIGH",""}{b:=true;p:=&b;e:="low";applyRequestReasoningEffort(v,&p,&e);emit("reasoning",v,map[string]any{"thinking":*p,"effort":e})}
for _,v:=range []string{"en-US,en;q=0.8"," zh-TW ;q=0.9,en","","xx-Test"}{t:=parseFirstLanguageTag(v);if t==""{t="zh-CN"};emit("locale",v,LanguageLocaleName(t))}
for _,v:=range []string{"LOCAL","  ",""}{c:=&ChatManage{Intent:"greeting",IntentPromptOverrides:map[string]string{"greeting":v}};applyIntentPromptOverride(c,map[string]string{"greeting":"GLOBAL"});emit("intent",v,c.SystemPromptOverride)}
for _,v:=range []string{"dedicated","missing","baseline"}{s:=&ModelService{IDs:map[string]bool{"baseline":true,"dedicated":true}};p:=&PluginQueryUnderstand{modelService:s};m,i:=p.selectModel(context.Background(),&ChatManage{ChatModelID:"baseline",QueryUnderstandModelID:v},false);emit("model",v,map[string]any{"id":m.ID,"images":i,"calls":s.Calls})}
p:=&PluginQueryUnderstand{config:&Config{Conversation:&Conversation{RewritePromptSystem:"START {{language}} {{conversation}}",RewritePromptUser:"START {{query}}"}}};c:=&ChatManage{Query:"q0",Language:"Korean",RewritePromptSystem:"SESSION {{language}} {{conversation}}",RewritePromptUser:"SESSION {{query}}",Memory:" BACKGROUND"};s,u:=p.buildPrompts(context.Background(),c,[]*History{{Query:"old",Answer:"answer"}});emit("prompts",nil,map[string]string{"system":s,"user":u})
}
"""
w=Path('E:/WK/oracle/configuration-qa-20261015');w.mkdir(parents=True,exist_ok=True);(w/'main.go').write_text(code,encoding='utf-8');(w/'go.mod').write_text('module configurationoracle\n\ngo 1.26.0\n')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','run','.'],cwd=w,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
Path('docs/weknora-port/evidence/QA_ENTRY_20261015/configuration.go.stdout.txt').write_bytes(r.stdout);Path('docs/weknora-port/evidence/QA_ENTRY_20261015/configuration.go.stderr.txt').write_bytes(r.stderr)
rows=[json.loads(x[7:]) for x in r.stdout.decode().splitlines() if x.startswith('ORACLE ')]
value={'upstream':PIN,'exit_code':r.returncode,'source_sha256':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in [('config.go',conf),('query_understand.go',q),('context_helpers.go',lang),('language.go',middleware),('api_types.go',api),('session_qa_helpers.go',h)]},'scope':'Exact fixed loader/FindTemplate/selectModel/buildPrompts/intent/locale/reasoning functions. DTO/model lookup/logger/background and deterministic placeholder seams; not full Go engine or Agent execution.','rows':rows,'real_provider_calls':0}
Path('tests/weknora_port/fixtures/configuration_go_qa_20261015.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps({'exit_code':r.returncode,'rows':len(rows)}));raise SystemExit(r.returncode)
