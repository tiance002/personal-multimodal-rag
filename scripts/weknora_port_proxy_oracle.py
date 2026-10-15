"""Run pinned standard-library proxy decisions and in-memory CONNECT oracle."""
from __future__ import annotations
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SDK=Path("E:/RAG-reference/toolchains/go1.26.0/go")
UPSTREAM=Path("E:/RAG-reference/WeKnora-3e8b0bfc")
WORK=Path("E:/WK/run/proxy-boundary-20261015")
EVIDENCE=ROOT/"docs/weknora-port/evidence/PROXY_BOUNDARY_20261015"
CASES=[
 {"name":"mapped-cidr-v6","url":"https://[::ffff:8.8.8.8]/","env":{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":"::ffff:8.8.0.0/112"}},
 {"name":"mapped-cidr-v4","url":"https://8.8.8.8/","env":{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":"::ffff:8.8.0.0/112"}},
 {"name":"https-upper","url":"https://api.example/v1","env":{"HTTPS_PROXY":"http://proxy.example:8080"}},
 {"name":"https-lower","url":"https://api.example/v1","env":{"https_proxy":"http://proxy.example:8080"}},
 {"name":"http-upper","url":"http://api.example/v1","env":{"HTTP_PROXY":"http://proxy.example:8080"}},
 {"name":"http-lower","url":"http://api.example/v1","env":{"http_proxy":"http://proxy.example:8080"}},
 {"name":"legacy-ignored","url":"https://api.example/v1","env":{"https":"http://proxy.example:8080","no":"*"}},
 {"name":"all-ignored","url":"https://api.example/v1","env":{"ALL_PROXY":"http://proxy.example:8080"}},
 {"name":"cgi-before-no-proxy","url":"http://api.example/v1","env":{"HTTP_PROXY":"http://proxy.example:8080","REQUEST_METHOD":"GET","NO_PROXY":"*"}},
 {"name":"cgi-before-localhost","url":"http://localhost/v1","env":{"HTTP_PROXY":"http://proxy.example:8080","REQUEST_METHOD":"GET"}},
 {"name":"cgi-https-allowed","url":"https://api.example/v1","env":{"HTTPS_PROXY":"http://proxy.example:8080","REQUEST_METHOD":"GET"}},
 {"name":"idna-unicode-rule","url":"https://xn--fa-hia.example/v1","env":{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":"faß.example"}},
 {"name":"idna-unicode-url","url":"https://faß.example/v1","env":{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":"xn--fa-hia.example"}},
 {"name":"cache-empty-first","url":"https://api.example/v1","env":{},"after":{"HTTPS_PROXY":"http://changed.example:8080"}},
 {"name":"cache-original-first","url":"https://api.example/v1","env":{"HTTPS_PROXY":"http://proxy.example:8080"},"after":{"HTTPS_PROXY":"http://changed.example:8080","NO_PROXY":"*"}},
]
TRUST=[
 ("http://proxy.example:8080","proxy.example:8080"),
 ("proxy.example:8080","proxy.example:8080"),
 ("http://proxy.example","proxy.example:80"),
 ("http://Proxy.example:8080","proxy.example:8080"),
 ("http://u:p@proxy.example:8080","proxy.example:8080"),
 ("http://proxy.example:8080","8.8.8.8:8080"),
 ("http://proxy.example:8080\n","proxy.example:8080"),
 (" http://proxy.example:8080","proxy.example:8080"),
 ("http://proxy|bad.example:8080","proxy|bad.example:8080"),
 ("http://u space:p@proxy.example:8080","proxy.example:8080"),
]

def main():
 assert (SDK/"VERSION").read_text(encoding="utf-8").splitlines()[0]=="go1.26.0"
 for name in ("tmp","gocache","gopath","gomodcache","telemetry"):(WORK/name).mkdir(parents=True,exist_ok=True)
 EVIDENCE.mkdir(parents=True,exist_ok=True)
 (WORK/"telemetry/mode").write_text("off\n",encoding="utf-8")
 env=dict(os.environ,GOCACHE=str(WORK/"gocache"),GOTMPDIR=str(WORK/"tmp"),GOPATH=str(WORK/"gopath"),GOMODCACHE=str(WORK/"gomodcache"),TEMP=str(WORK/"tmp"),TMP=str(WORK/"tmp"),GOENV="off",GO111MODULE="off",GOTOOLCHAIN="local",GOPROXY="off",GOSUMDB="off",CGO_ENABLED="0",GODEBUG="asynctimerchan=0",TEST_TELEMETRY_DIR=str(WORK/"telemetry"))
 for key in list(env):
  if key.lower() in ("http_proxy","https_proxy","no_proxy","all_proxy","http","https","no","request_method"):env.pop(key,None)
 source=(UPSTREAM/"internal/utils/security.go").read_text(encoding="utf-8")
 function=re.search(r"func IsSystemProxy\(host string\) bool \{[\s\S]*?^\}",source,re.M).group(0)
 program=r'''package main
import("encoding/json";"net/http";"net/url";"os")
type proxyConfig struct{HTTPProxy,HTTPSProxy string}
func first(names ...string)string{for _,name:=range names{if value:=os.Getenv(name);value!=""{return value}};return ""}
var httpproxy=struct{FromEnvironment func()proxyConfig}{func()proxyConfig{return proxyConfig{first("HTTP_PROXY","http_proxy"),first("HTTPS_PROXY","https_proxy")}}}
SOURCE_FUNCTION
type Input struct{URL string `json:"url"`;Env map[string]string `json:"env"`;After map[string]string `json:"after"`;Address string `json:"address"`}
func decision(raw string)map[string]any{u,e:=url.Parse(raw);if e!=nil{panic(e)};p,e:=http.ProxyFromEnvironment(&http.Request{URL:u});out:=map[string]any{"error":e!=nil,"proxy":nil};if p!=nil{out["proxy"]=p.String()};return out}
func main(){var input Input;if e:=json.NewDecoder(os.Stdin).Decode(&input);e!=nil{panic(e)};for k,v:=range input.Env{os.Setenv(k,v)};out:=map[string]any{"first":decision(input.URL)};if input.After!=nil{for k,v:=range input.After{os.Setenv(k,v)};out["second"]=decision(input.URL)};if input.Address!=""{out["system_proxy"]=IsSystemProxy(input.Address)};json.NewEncoder(os.Stdout).Encode(out)}
'''.replace("SOURCE_FUNCTION",function)
 (WORK/"proxy_env_oracle.go").write_text(program,encoding="utf-8")
 go=str(SDK/"bin/go.exe");binary=WORK/"proxy-env-oracle.exe"
 def run(args,label,**kwargs):
  base=label;attempt=1
  while (EVIDENCE/(label+".stdout.txt")).exists():
   attempt+=1;label=base+"-attempt"+str(attempt)
  print(label,flush=True)
  result=subprocess.run(args,cwd=WORK,env=env,capture_output=True,text=True,encoding="utf-8",**kwargs)
  (EVIDENCE/(label+".stdout.txt")).write_text(result.stdout,encoding="utf-8")
  (EVIDENCE/(label+".stderr.txt")).write_text(result.stderr,encoding="utf-8")
  (EVIDENCE/(label+".exitcode.txt")).write_text(str(result.returncode)+"\n",encoding="utf-8")
  if result.returncode:raise RuntimeError(label+" failed: "+result.stderr[-1000:])
  return result
 run([go,"build","-o",str(binary),str(WORK/"proxy_env_oracle.go")],"go-env-build",timeout=240)
 records=[]
 for item in CASES:
  result=run([str(binary)],"env-"+item["name"],input=json.dumps(item,ensure_ascii=False),timeout=10)
  records.append({"input":item,"observed":json.loads(result.stdout)})
 trust=[]
 for i,(raw,address) in enumerate(TRUST):
  item={"url":"https://api.example","env":{"HTTPS_PROXY":raw},"address":address}
  result=run([str(binary)],"trust-"+str(i),input=json.dumps(item),timeout=10)
  trust.append({"input":item,"observed":json.loads(result.stdout)})
 (WORK/"proxy_connect_oracle_test.go").write_bytes((ROOT/"tests/weknora_port/fixtures/proxy_connect_oracle_test.go").read_bytes())
 connect=run([go,"test","-v",str(WORK/"proxy_env_oracle.go"),str(WORK/"proxy_connect_oracle_test.go")],"go-connect",timeout=240)
 protocol=[json.loads(row.split(":",1)[1]) for row in connect.stdout.splitlines() if row.startswith("PROXY_CONNECT_ORACLE_JSON:")]
 assert len(protocol)==9
 files=[UPSTREAM/"internal/models/api/transport.go",UPSTREAM/"internal/utils/security.go",SDK/"src/net/http/transport.go",SDK/"src/net/url/url.go",SDK/"src/vendor/golang.org/x/net/http/httpproxy/proxy.go",ROOT/"tests/weknora_port/fixtures/proxy_connect_oracle_test.go"]
 payload={"kind":"REAL_GO126_STANDARD_PROXY_DECISIONS_AND_IN_MEMORY_TRANSPORT_CONNECT_WITH_SOURCE_EXTRACTED_ENV_SHIM","upstream":"3e8b0bfc80b845b2d4b2ed683994748741450a97","go_version":"go1.26.0","files":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},"source_function_sha256":hashlib.sha256(function.encode()).hexdigest(),"generated_go_sha256":hashlib.sha256(program.encode()).hexdigest(),"cases":records,"trust":trust,"connect":protocol,"real_network_calls":0,"cache_root":str(WORK),"windows_case_insensitive_environment_limit":"Simultaneous differing-case variables are SOURCE_READ only; separate standard-key cases executed."}
 (ROOT/"tests/weknora_port/fixtures/proxy_go_oracle.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 (EVIDENCE/"source-env-shim.go").write_text(program,encoding="utf-8")
 print("Oracle fixture written",flush=True)
if __name__=="__main__":main()
