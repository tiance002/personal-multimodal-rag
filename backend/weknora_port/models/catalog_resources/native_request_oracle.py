"""Execute exact pinned request.go bodies in a minimal offline Go module.

Only unused application metadata types and an identity image resolver are
substituted. No HTTP client/server, provider request or model key exists.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PIN="3e8b0bfc80b845b2d4b2ed683994748741450a97"
PROGRAM=r'''package main
import("encoding/json";"fmt";"github.com/Tencent/WeKnora/internal/models/api";r "github.com/Tencent/WeKnora/internal/models/api/openairesponses";a "github.com/Tencent/WeKnora/internal/models/api/anthropicmessages";g "github.com/Tencent/WeKnora/internal/models/api/googlegenai")
func main(){
raw:=`[{"role":"system","content":"Use evidence."},{"role":"user","content":"Question"},{"role":"assistant","content":"Answer","reasoning_content":"edited","reasoning_metadata":{"anthropic_thinking_blocks":[{"type":"thinking","thinking":"opaque <| token","signature":"SIG"}],"openai_responses_reasoning":[{"type":"reasoning","id":"r1","encrypted_content":"opaque"}]},"tool_calls":[{"id":"c1","type":"function","function":{"name":"lookup","arguments":"{}"},"provider_metadata":{"thought_signature":"SIG","openai_responses_item_id":"i1"}}]},{"role":"tool","content":"{\"x\":1}","tool_call_id":"c1"}]`
var messages []api.Message;if err:=json.Unmarshal([]byte(raw),&messages);err!=nil{panic(err)}
for _,protocol:=range []string{"openai-responses","anthropic-messages","google-generative-ai"}{
for _,level:=range []api.ReasoningEffort{"","off","auto","minimal","low","medium","high","xhigh","max"}{for _,stream:=range []bool{false,true}{
opts:=&api.Options{MaxTokens:256,Temperature:0.5,TopP:0.9,Seed:5,ReasoningEffort:level,CacheRetention:api.CacheRetentionNone,ToolChoice:"required",Tools:[]api.Tool{{Type:"function",Function:api.FunctionDef{Name:"lookup",Description:"Search",Parameters:json.RawMessage(`{"type":"object","properties":{"title":{"type":"string","default":"x"}},"additionalProperties":false}`)}}}}
ep:=api.Endpoint{Model:"m",ModelID:"m"};var body map[string]any;var err error
switch protocol{case "openai-responses":body,err=r.New(r.Config{Endpoint:ep,Settings:api.DefaultOpenAIResponses(),Reasoning:true}).BuildRequestBody(messages,opts,stream)
case "anthropic-messages":body,err=a.New(a.Config{Endpoint:ep,Settings:api.DefaultAnthropicMessages(),Reasoning:true}).BuildRequestBody(messages,opts,stream)
case "google-generative-ai":body,err=g.New(g.Config{Endpoint:ep,Settings:api.DefaultGoogleGenerativeAI(),Reasoning:true}).BuildRequestBody(messages,opts,stream)}
if err!=nil{panic(err)};line,_:=json.Marshal(map[string]any{"api":protocol,"level":level,"stream":stream,"messages":messages,"body":body});fmt.Println(string(line))
}}}}
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--upstream',type=Path,required=True)
    parser.add_argument('--go',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if subprocess.check_output(['git','-C',str(args.upstream),'rev-parse','HEAD']).decode().strip()!=PIN:
        raise RuntimeError('ORACLE_PIN_MISMATCH')
    dest=Path.cwd()/'var/weknora-port/native-request-oracle'
    dest.mkdir(parents=True,exist_ok=True)
    hashes={}
    def source(path):
        raw=(args.upstream/path).read_bytes();hashes[path]=hashlib.sha256(raw).hexdigest()
        return raw.decode('utf-8')
    def write(path,text):
        target=dest/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(text,encoding='utf-8')
    write('go.mod','module github.com/Tencent/WeKnora\ngo 1.26\n')
    write('internal/types/types.go','package types\nimport "encoding/json"\ntype ProviderMetadata map[string]json.RawMessage\ntype ToolCallMetadata map[string]json.RawMessage\nconst FinishReasonIncomplete="incomplete"\n')
    for file in ('types.go','compat_settings.go','special_tokens.go'):
        write('internal/models/api/'+file,source('internal/models/api/'+file))
    options=source('internal/models/api/options.go')
    write('internal/models/api/options.go','package api\nimport "encoding/json"\n'+options[options.index('// CacheRetention is'):])
    cache=source('internal/models/api/prompt_cache.go')
    cache=cache[cache.index('const openAIPromptCacheKeyMaxLength'):cache.index('// ApplyCacheControlBreakpoints')]
    write('internal/models/api/prompt_cache.go','package api\n'+cache)
    write('internal/models/api/endpoint.go','package api\ntype Endpoint struct{Model,ModelID string}\nfunc ResolveImageURLForLLM(s string)string{return s}\n')
    for package in ('openairesponses','anthropicmessages','googlegenai'):
        write('internal/models/api/'+package+'/request.go',source('internal/models/api/'+package+'/request.go'))
        write('internal/models/api/'+package+'/client_stub.go','package '+package+'\ntype Client struct{cfg Config}\nfunc New(cfg Config)*Client{return &Client{cfg:cfg}}\n')
    write('main.go',PROGRAM)
    result=subprocess.run([str(args.go),'run','.'],cwd=dest,text=True,encoding='utf-8',capture_output=True)
    (dest/'execution.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        print(result.stderr);raise SystemExit(result.returncode)
    cases=[json.loads(line) for line in result.stdout.splitlines()]
    assert len(cases)==54
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'upstream':PIN,'source_hashes':hashes,'kind':'exact-request-source-with-neutral-type-stubs',
        'provider_calls':0,'exit_code':0,'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('SOURCE_EXTRACTED_REQUEST_ORACLE_PASS: 54 actual Go request cases; 0 provider calls')


if __name__=='__main__':main()
