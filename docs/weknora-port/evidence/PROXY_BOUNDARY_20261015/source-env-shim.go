package main
import("encoding/json";"net/http";"net/url";"os")
type proxyConfig struct{HTTPProxy,HTTPSProxy string}
func first(names ...string)string{for _,name:=range names{if value:=os.Getenv(name);value!=""{return value}};return ""}
var httpproxy=struct{FromEnvironment func()proxyConfig}{func()proxyConfig{return proxyConfig{first("HTTP_PROXY","http_proxy"),first("HTTPS_PROXY","https_proxy")}}}
func IsSystemProxy(host string) bool {
	proxyCfg := httpproxy.FromEnvironment()
	for _, proxyUrl := range []string{
		proxyCfg.HTTPProxy,
		proxyCfg.HTTPSProxy,
	} {
		if proxyUrl == "" {
			continue
		}
		if parse, err := url.Parse(proxyUrl); err == nil {
			if parse.Host == host {
				return true
			}
		}
	}
	return false
}
type Input struct{URL string `json:"url"`;Env map[string]string `json:"env"`;After map[string]string `json:"after"`;Address string `json:"address"`}
func decision(raw string)map[string]any{u,e:=url.Parse(raw);if e!=nil{panic(e)};p,e:=http.ProxyFromEnvironment(&http.Request{URL:u});out:=map[string]any{"error":e!=nil,"proxy":nil};if p!=nil{out["proxy"]=p.String()};return out}
func main(){var input Input;if e:=json.NewDecoder(os.Stdin).Decode(&input);e!=nil{panic(e)};for k,v:=range input.Env{os.Setenv(k,v)};out:=map[string]any{"first":decision(input.URL)};if input.After!=nil{for k,v:=range input.After{os.Setenv(k,v)};out["second"]=decision(input.URL)};if input.Address!=""{out["system_proxy"]=IsSystemProxy(input.Address)};json.NewEncoder(os.Stdout).Encode(out)}
