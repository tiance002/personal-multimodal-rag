package main

import (
 "bufio"
 "context"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "net"
 "net/http"
 "net/url"
 "strings"
 "testing"
 "testing/synctest"
 "time"
)

func TestSourceConnectDeadlines(t *testing.T) {
 for _, mode := range []string{"write-block","headers-block","trickle","short-parent","long-parent","malformed","complete","non200-lf-201","non200-lf-299"} {
  t.Run(mode,func(t *testing.T) {
   synctest.Test(t,func(t *testing.T) {
    began:=time.Now(); client,peer:=net.Pipe(); finish:=make(chan struct{}); peerDone:=make(chan struct{})
    callback:=false; proxy,_:=url.Parse("http://proxy.fixture.invalid:8080")
    sentinel:=errors.New("FIXTURE_COMPLETE_HEADERS_STOP_BEFORE_TLS")
    transport:=&http.Transport{Proxy:http.ProxyURL(proxy),DialContext:func(_ context.Context,_,addr string)(net.Conn,error){if addr!="proxy.fixture.invalid:8080"{t.Errorf("unexpected sink")};return client,nil},OnProxyConnectResponse:func(_ context.Context,_ *url.URL,_ *http.Request,resp *http.Response)error{if resp.StatusCode==200{callback=true;return sentinel};return nil}}
    go func(){
     defer close(peerDone);defer peer.Close()
     if mode=="write-block" {<-finish;return}
     reader:=bufio.NewReader(peer)
     for {line,err:=reader.ReadString('\n');if err!=nil{return};if line=="\r\n"{break}}
     if mode=="trickle" {time.Sleep(45*time.Second)}
     if mode=="non200-lf-201" || mode=="non200-lf-299" {status:=201;if mode=="non200-lf-299"{status=299};fmt.Fprintf(peer,"HTTP/1.1 %d Fixture\n\n",status);return}
     if mode=="malformed" {io.WriteString(peer,"HTTP/1.1 200 OK\r\nbroken\r\n\r\n");return}
     io.WriteString(peer,"HTTP/1.1 200 Connection Established\r\n")
     if mode=="headers-block" || mode=="short-parent" || mode=="long-parent" {<-finish;return}
     if mode=="trickle" {time.Sleep(20*time.Second)}
     io.WriteString(peer,"X-Fixture: yes\r\n\r\n")
    }()
    parent:=300*time.Second;if mode=="short-parent"{parent=12*time.Second};if mode=="long-parent"{parent=600*time.Second}
    ctx,cancel:=context.WithTimeout(context.Background(),parent)
    request,_:=http.NewRequestWithContext(ctx,"POST","https://api.fixture.invalid/v1",strings.NewReader("MODEL_BODY"))
    response,err:=transport.RoundTrip(request);elapsed:=time.Since(began)
    close(finish);cancel();client.Close();transport.CloseIdleConnections();<-peerDone
    if response!=nil{response.Body.Close()}
    expected:=60*time.Second;if mode=="short-parent"{expected=12*time.Second}
    if mode=="malformed" || mode=="complete" || strings.HasPrefix(mode,"non200-lf-") {expected=0}
    if elapsed!=expected {t.Fatalf("elapsed %v, expected %v",elapsed,expected)}
    if (mode=="complete") != callback {t.Fatalf("callback %v",callback)}
    if err==nil {t.Fatal("all fixture paths must stop before target TLS/body")}
    payload,_:=json.Marshal(map[string]any{"case":mode,"elapsed_seconds":elapsed.Seconds(),"callback_after_complete_headers":callback,"request_failed_before_target_tls":true,"real_network_calls":0})
    fmt.Println("PROXY_CONNECT_ORACLE_JSON:"+string(payload))
   })
  })
 }
}
