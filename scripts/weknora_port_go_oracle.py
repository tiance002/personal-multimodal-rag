"""Run exact fixed upstream SSEReader in a standalone Go stdlib oracle.

No upstream algorithm edits, paid model calls, production imports or database.
Tencent upstream MIT source attribution retained in generated oracle directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PIN = '3e8b0bfc80b845b2d4b2ed683994748741450a97'
CASES = [
    ': ping\nid: 4\nevent: ignored\ndata: {"a":1}\n\ndata:[DONE]  \n',
    'data: first\ndata: second\n',
    'event: stop\ndata: \ndata:  spaced \r\n',
    'data:{"text":"中文"}\r\ndata: [DONE]',
    'data: final-without-newline',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--go', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def git(*argv):
        return subprocess.check_output(['git', '-C', str(args.upstream), *argv])
    if git('rev-parse', 'HEAD').decode().strip() != PIN:
        raise RuntimeError('ORACLE_UPSTREAM_MISMATCH')
    source = git('show', 'HEAD:internal/models/api/sse.go')
    object_hash = hashlib.sha1(b'blob '+str(len(source)).encode()+b'\0'+source).hexdigest()
    if object_hash != git('rev-parse', 'HEAD:internal/models/api/sse.go').decode().strip():
        raise RuntimeError('ORACLE_SOURCE_HASH_MISMATCH')
    oracle = Path.cwd() / 'var/weknora-port/oracle/sse'
    oracle.mkdir(parents=True, exist_ok=True)
    (oracle/'sse.go').write_bytes(source)
    (oracle/'go.mod').write_text('module weknora_sse_oracle\n\ngo 1.26.0\n')
    (oracle/'NOTICE').write_text('Tencent/WeKnora@'+PIN+' internal/models/api/sse.go copied unchanged. Copyright (C)2025Tencent. MIT; see fixed upstream LICENSE. Oracle only, not product backend.\n')
    # Feed fixtures into a standard-library test; JSON encoding preserves Unicode.
    cases = ',\n'.join(json.dumps(x, ensure_ascii=False) for x in CASES)
    program = '''package api
import("encoding/json";"fmt";"io";"strings";"testing")
func TestEmitOracle(t *testing.T){
cases:=[]string{CASES,}
for i,s:=range cases {
r:=NewSSEReader(strings.NewReader(s));rows:=[]map[string]any{}
for {e,err:=r.ReadEvent();if err==io.EOF {break};if err!=nil {t.Fatal(err)}
rows=append(rows,map[string]any{"done":e.Done,"data":string(e.Data)});if e.Done {break}}
b,err:=json.Marshal(map[string]any{"case":i,"events":rows});if err!=nil {t.Fatal(err)};fmt.Println("ORACLE "+string(b))
}}
'''.replace('CASES',cases)
    (oracle/'sse_oracle_test.go').write_text(program,encoding='utf-8')
    version = subprocess.check_output([str(args.go),'version']).decode().strip()
    result = subprocess.run([str(args.go),'test','-v','-run','TestEmitOracle','.'],cwd=oracle,
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    text = result.stdout.decode('utf-8',errors='replace')
    (oracle/'execution.log').write_text(text,encoding='utf-8')
    if result.returncode != 0:
        raise RuntimeError('GO_ORACLE_FAILED:'+str(result.returncode))
    observed = [json.loads(line.removeprefix('ORACLE ')) for line in text.splitlines() if line.startswith('ORACLE ')]
    if len(observed) != len(CASES):
        raise RuntimeError('ORACLE_OUTPUT_INCOMPLETE')
    payload = dict(upstream=PIN,source='internal/models/api/sse.go:ReadEvent',blob=object_hash,
                   toolchain=version,exit_code=result.returncode,scope='UNMODIFIED_SSE_READER_ONLY',
                   cases=[dict(input=CASES[row['case']],events=row['events']) for row in observed])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='GO_ORACLE_EXECUTED',cases=len(observed),toolchain=version,provider_calls=0)))


if __name__ == '__main__':
    main()
