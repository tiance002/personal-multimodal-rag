"""Exact fixed affinity plugin and normal QA model selectors, with IO DTO seams."""
import os,json,hashlib,subprocess
from pathlib import Path
ROOT='E:/RAG-reference/WeKnora-3e8b0bfc';PIN='3e8b0bfc80b845b2d4b2ed683994748741450a97'
assert subprocess.check_output(['git','-C',ROOT,'rev-parse','HEAD']).decode().strip()==PIN
def src(n):return subprocess.check_output(['git','-C',ROOT,'show',PIN+':'+n]).decode('utf-8')
def fn(s,p):
 a=s.index(p);return s[a:s.index('\n}',a)+2]
aff=src('internal/application/service/chat_pipeline/memory_affinity.go');qa=src('internal/application/service/session_knowledge_qa.go');helper=src('internal/application/service/session_qa_helpers.go')
const=aff[aff.index('const ('):aff.index('\n)',aff.index('const ('))+2].replace('types.','')
code='package main\nimport("context";"strings";"fmt";"math";"sort";"encoding/json";"os")\n'+const+'\n'
code+=r"""
const MemoryDocAffinityMinHits=2
const ModelSourceRemote="remote"
const ModelTypeKnowledgeQA="KnowledgeQA"
const BuiltinWikiFixerID="wiki-fixer"
type EventType string
type PluginError struct{Message string}
type SearchResult struct{ID string;KnowledgeID string;Score float64}
type ChatManage struct{RerankResult []*SearchResult}
type MemoryService struct{Hits map[string]int;Asked []string}
func(m *MemoryService)DocumentAffinity(ctx context.Context,ids []string)map[string]int{m.Asked=ids;return m.Hits}
type PluginMemoryAffinity struct{memoryService *MemoryService}
type Model struct{ID string;Type string;Source string}
type KnowledgeBase struct{ID string;SummaryModelID string}
type Knowledge struct{KnowledgeBaseID string}
type Session struct{ID string}
type AgentConfig struct{ModelID string}
type CustomAgent struct{ID string;Config AgentConfig}
type QARequest struct{SummaryModelID string;CustomAgent *CustomAgent;Session *Session;SharedAgentReadOnly bool}
type ModelService struct{Models map[string]*Model;List []*Model}
func(s *ModelService)GetModelByID(ctx context.Context,id string)(*Model,error){m:=s.Models[id];if m==nil{return nil,fmt.Errorf("missing")};return m,nil}
func(s *ModelService)ListModels(ctx context.Context)([]*Model,error){return s.List,nil}
type KBService struct{Bases map[string]*KnowledgeBase}
func(s *KBService)GetKnowledgeBaseByID(ctx context.Context,id string)(*KnowledgeBase,error){b:=s.Bases[id];if b==nil{return nil,fmt.Errorf("missing")};return b,nil}
type KnowledgeService struct{}
func(*KnowledgeService)GetKnowledgeBatchWithSharedAccess(context.Context,uint64,[]string)([]*Knowledge,error){return nil,nil}
func MustTenantIDFromContext(context.Context)uint64{return 1}
type sessionService struct{modelService *ModelService;knowledgeBaseService *KBService;knowledgeService *KnowledgeService}
type Logger struct{}
var logger Logger
func(Logger)Info(context.Context,...any){}
func(Logger)Infof(context.Context,string,...any){}
func(Logger)Warnf(context.Context,string,...any){}
func(Logger)Error(context.Context,...any){}
func(Logger)Errorf(context.Context,string,...any){}
"""
for text,prefix in [(aff,'func (p *PluginMemoryAffinity) OnEvent('),(aff,'func affinityFactor('),(qa,'func (s *sessionService) selectChatModelID('),(helper,'func (s *sessionService) resolveChatModelID(')]:code+=fn(text,prefix).replace('types.','')+'\n'
code+=r"""
type Case struct{Kind string;Rows []*SearchResult;Hits map[string]int;ModelIDs []string;Request string;Profile string;KBIDs []string;Models []*Model;Bases []*KnowledgeBase}
func main(){var cases []Case;if e:=json.NewDecoder(os.Stdin).Decode(&cases);e!=nil{panic(e)};out:=[]any{};for _,c:=range cases{if c.Kind=="affinity"{service:=&MemoryService{Hits:c.Hits};p:=&PluginMemoryAffinity{memoryService:service};cm:=&ChatManage{RerankResult:c.Rows};p.OnEvent(context.Background(),"CHUNK_RERANK",cm,func()*PluginError{return nil});out=append(out,map[string]any{"kind":c.Kind,"rows":cm.RerankResult,"asked":service.Asked})}else{models:=&ModelService{Models:map[string]*Model{},List:c.Models};for _,m:=range c.Models{models.Models[m.ID]=m};bases:=&KBService{Bases:map[string]*KnowledgeBase{}};for _,b:=range c.Bases{bases.Bases[b.ID]=b};s:=&sessionService{modelService:models,knowledgeBaseService:bases,knowledgeService:&KnowledgeService{}};req:=&QARequest{SummaryModelID:c.Request,Session:&Session{ID:"s"}};if c.Profile!=""{req.CustomAgent=&CustomAgent{ID:"ordinary-profile",Config:AgentConfig{ModelID:c.Profile}}};id,e:=s.resolveChatModelID(context.Background(),req,c.KBIDs,nil);out=append(out,map[string]any{"kind":c.Kind,"id":id,"error":e!=nil})}};json.NewEncoder(os.Stdout).Encode(out)}
"""
cases=[{'Kind':'affinity','Rows':[{'ID':'a','KnowledgeID':'stranger','Score':.8},{'ID':'b','KnowledgeID':'familiar','Score':.78}],'Hits':{'familiar':8}}, {'Kind':'affinity','Rows':[{'ID':'a','KnowledgeID':'relevant','Score':.9},{'ID':'b','KnowledgeID':'familiar','Score':.4}],'Hits':{'familiar':1000}}, {'Kind':'affinity','Rows':[{'ID':'a','KnowledgeID':'d','Score':1.0},{'ID':'b','KnowledgeID':'d','Score':1.0},{'ID':'empty','KnowledgeID':'','Score':.3}],'Hits':{'d':2}}, {'Kind':'affinity','Rows':[{'ID':'a','KnowledgeID':'d','Score':.8}],'Hits':{'d':1}}, {'Kind':'affinity','Rows':[],'Hits':{'d':8}}]
models=[{'ID':'cheap','Type':'KnowledgeQA','Source':'siliconflow'},{'ID':'expensive','Type':'KnowledgeQA','Source':'deepseek'},{'ID':'remote','Type':'KnowledgeQA','Source':'remote'},{'ID':'embed','Type':'Embedding','Source':'remote'}]
for requested,profile,kbs,bases in [('', '',['b','a'],[{'ID':'a','SummaryModelID':'cheap'},{'ID':'b','SummaryModelID':'expensive'}]),('', '',['a','b'],[{'ID':'a','SummaryModelID':'expensive'},{'ID':'b','SummaryModelID':'remote'}]),('cheap','expensive',['b'],[{'ID':'b','SummaryModelID':'remote'}]),('missing','expensive',['b'],[{'ID':'b','SummaryModelID':'remote'}]),('embed','',['b'],[{'ID':'b','SummaryModelID':'expensive'}]),('cheap','missing',['b'],[{'ID':'b','SummaryModelID':'remote'}]),('', '',['a'],[{'ID':'a','SummaryModelID':''}])]:cases.append({'Kind':'model','Request':requested,'Profile':profile,'KBIDs':kbs,'Bases':bases,'Models':models})
work=Path('E:/WK/oracle/affinity-kb');work.mkdir(parents=True,exist_ok=True);(work/'go.mod').write_text('module affinitykb\n\ngo 1.26.0\n');(work/'main.go').write_text(code,encoding='utf-8')
env=dict(os.environ,GOCACHE=str(Path('var/weknora-port/oracle/go-cache').resolve()),GOPROXY='off',GOSUMDB='off')
r=subprocess.run(['E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe','run','.'],cwd=work,env=env,input=json.dumps(cases).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
Path('docs/weknora-port/evidence/P4_AFFINITY_KB_GO_20261014.stderr.txt').write_bytes(r.stderr)
value={'upstream':PIN,'source_sha256':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in [('memory_affinity.go',aff),('session_knowledge_qa.go',qa),('session_qa_helpers.go',helper)]},'cases':cases,'rows':json.loads(r.stdout) if r.returncode==0 else [],'exit_code':r.returncode,'scope':'Exact affinity OnEvent/factor and selectChatModelID/resolveChatModelID functions; stub DTO/model/KB/knowledge services/logger. Not full Go engine.','real_provider_calls':0}
Path('tests/weknora_port/fixtures/affinity_kb_go_oracle.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps({'exit_code':r.returncode,'cases':len(value['rows'])}));raise SystemExit(r.returncode)
