"""Execute fixed upstream BuildRequestBody, never call a provider.

The upstream repository remains read-only. Compile a generated oracle in an
ignored exact-source copy; any dependency/platform failure is NOT a parity PASS.
"""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

PIN = '3e8b0bfc80b845b2d4b2ed683994748741450a97'

PROGRAM = r'''package openaicompletions
import("encoding/json";"fmt";"testing";"github.com/Tencent/WeKnora/internal/models/api")
func TestPythonPortRequestOracle(t *testing.T) {
off:=false
for _,vendor:=range []string{"openai-compatible","siliconflow","deepseek"} {
settings:=api.DefaultOpenAICompletions()
if vendor=="siliconflow" {settings.MaxTokensField="max_tokens";settings.ThinkingFormat=api.ThinkingFormatEnableThinking;settings.ThinkingBudgetField="thinking_budget";settings.SupportsReasoningEffort=true;settings.PromptCacheAccounting=true}
if vendor=="deepseek" {settings.MaxTokensField="max_tokens";settings.ThinkingFormat=api.ThinkingFormatThinkingType;settings.SupportsReasoningEffort=true;settings.PromptCacheAccounting=true;settings.SupportsStore=false;settings.ToolChoiceModes=[]string{"none","auto"}}
c:=New(Config{Endpoint:api.Endpoint{BaseURL:"https://fixture.example/v1",Model:"m",ModelID:"id",Auth:api.BearerAuth("SYNTHETIC-NOT-A-KEY")},Settings:settings})
for _,stream:=range []bool{false,true} {
body,err:=c.BuildRequestBody([]api.Message{{Role:"system",Content:"Answer from evidence."},{Role:"user",Content:"Public fixture"}},&api.Options{Temperature:0.3,MaxCompletionTokens:1024,Thinking:&off},stream)
if err!=nil {t.Fatal(err)};b,err:=json.Marshal(map[string]any{"provider":vendor,"stream":stream,"body":body});if err!=nil {t.Fatal(err)};fmt.Println("ORACLE "+string(b))
}}
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--go', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(['git','-C',str(args.upstream),'rev-parse','HEAD']).decode().strip()
    if head != PIN:
        raise RuntimeError('ORACLE_PIN_MISMATCH')
    dest = Path.cwd()/'var/weknora-port/oracle/provider'
    dest.mkdir(parents=True,exist_ok=True)
    marker = dest/'.fixed-source-pin'
    if not marker.exists():
        raw = subprocess.check_output(['git','-C',str(args.upstream),'archive','--format=zip','HEAD'])
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for member in z.infolist():
                path = (dest/member.filename).resolve()
                if not path.is_relative_to(dest.resolve()):
                    raise RuntimeError('ORACLE_ARCHIVE_PATH_INVALID')
            z.extractall(dest)
        marker.write_text(PIN)
    if marker.read_text()!=PIN:
        raise RuntimeError('ORACLE_COPY_MISMATCH')
    test = dest/'internal/models/api/openaicompletions/python_port_oracle_test.go'
    test.write_text(PROGRAM,encoding='utf-8')
    env = {k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('API_KEY','SECRET','PASSWORD','AUTHORIZATION'))}
    env.update(GOCACHE='E:/RAG-reference/go-cache',GOMODCACHE='E:/RAG-reference/go-modules',GOTOOLCHAIN='local')
    with (dest/'provider-oracle.log').open('wb') as log:
        result = subprocess.run([str(args.go),'test','-v','-run','^TestPythonPortRequestOracle$',
                                 './internal/models/api/openaicompletions'],cwd=dest,env=env,
                                stdout=log,stderr=subprocess.STDOUT)
    if result.returncode!=0:
        print(json.dumps(dict(status='PARITY_ORACLE_BLOCKED',exit_code=result.returncode,
                              log=str(dest/'provider-oracle.log'),provider_calls=0)))
        raise SystemExit(result.returncode)
    text = (dest/'provider-oracle.log').read_text(encoding='utf-8')
    cases = [json.loads(line[7:]) for line in text.splitlines() if line.startswith('ORACLE ')]
    if len(cases)!=6:
        raise RuntimeError('ORACLE_CASES_INCOMPLETE')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(upstream=PIN,source='openaicompletions/request.go:BuildRequestBody',
                                         exit_code=0,cases=cases),indent=2)+'\n')
    print(json.dumps(dict(status='GO_REQUEST_ORACLE_EXECUTED',cases=len(cases),provider_calls=0)))


if __name__=='__main__':
    main()
