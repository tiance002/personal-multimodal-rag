// Offline extractor of fixed-source Go literals. No provider packages execute.
package main

import (
 "encoding/json"
 "fmt"
 "go/ast"
 "go/parser"
 "go/token"
 "os"
 "path/filepath"
 "strconv"
 "strings"
)

var constants = map[string]ast.Expr{}
var active = map[string]bool{}

func evaluate(e ast.Expr) any {
 switch v := e.(type) {
 case *ast.BasicLit:
  if v.Kind == token.STRING { s, err := strconv.Unquote(v.Value); if err != nil { panic(err) }; return s }
  if v.Kind == token.INT { n, err := strconv.ParseInt(v.Value, 0, 64); if err != nil { panic(err) }; return n }
  if v.Kind == token.FLOAT { n, err := strconv.ParseFloat(v.Value, 64); if err != nil { panic(err) }; return n }
 case *ast.Ident:
  if v.Name == "true" { return true }; if v.Name == "false" { return false }; if v.Name == "nil" { return nil }
  if e, ok := constants[v.Name]; ok && !active[v.Name] { active[v.Name] = true; x := evaluate(e); delete(active, v.Name); return x }
  return map[string]any{"source_symbol":v.Name}
 case *ast.SelectorExpr:
  return evaluate(v.Sel)
 case *ast.UnaryExpr:
  if v.Op == token.AND { return evaluate(v.X) }; if v.Op == token.SUB { return -evaluate(v.X).(int64) }
 case *ast.BinaryExpr:
  left, right := evaluate(v.X), evaluate(v.Y)
  if a, ok := left.(string); ok && v.Op == token.ADD { return a+right.(string) }
  a, b := left.(int64), right.(int64)
  if v.Op == token.SHL { return a << b }; if v.Op == token.MUL { return a*b }; if v.Op == token.ADD { return a+b }
 case *ast.CallExpr:
  if len(v.Args) == 1 { return evaluate(v.Args[0]) }
 case *ast.CompositeLit:
  _, array := v.Type.(*ast.ArrayType)
  _, mapping := v.Type.(*ast.MapType)
  if array { out := []any{}; for _, e := range v.Elts { out = append(out, evaluate(e)) }; return out }
  out := map[string]any{}
  for _, e := range v.Elts { kv, ok := e.(*ast.KeyValueExpr); if !ok { panic(fmt.Sprintf("unexpected composite: %T", e)) }; var key string
   if n, ok := kv.Key.(*ast.Ident); ok && !mapping { key = n.Name } else { key = evaluate(kv.Key).(string) }
   out[key] = evaluate(kv.Value)
  }; return out
 case *ast.FuncLit:
  return map[string]any{"source_hook":true}
 }
 panic(fmt.Sprintf("unsupported source expression: %T", e))
}

func main() {
 if len(os.Args)!=3 { panic("usage: extract_registry upstream output") }
 root := os.Args[1]; files := map[string]*ast.File{}
 for _, dir := range []string{"internal/models/providers", "internal/models/api", "internal/models", "internal/types"} {
  entries, err := os.ReadDir(filepath.Join(root,dir)); if err != nil { panic(err) }
  for _, entry := range entries {
   if entry.IsDir() || !strings.HasSuffix(entry.Name(),".go") || strings.HasSuffix(entry.Name(),"_test.go") { continue }
   path := filepath.Join(root,dir,entry.Name()); f, err := parser.ParseFile(token.NewFileSet(),path,nil,0); if err != nil { panic(err) }; files[path]=f
   for _, d := range f.Decls { g, ok := d.(*ast.GenDecl); if !ok || g.Tok != token.CONST { continue }; for _, s := range g.Specs { vs := s.(*ast.ValueSpec); for i,n := range vs.Names { if i<len(vs.Values) { constants[n.Name]=vs.Values[i] } } } }
  }
 }
 out := map[string]any{}
 defaults := map[string]any{}
 fields := map[string]any{}
 for path,f := range files {
  if !strings.Contains(filepath.ToSlash(path),"internal/models/api/") { continue }
  for _,d := range f.Decls {
   if fn, ok := d.(*ast.FuncDecl); ok && (fn.Name.Name=="DefaultOpenAICompletions" || fn.Name.Name=="DefaultOpenAIResponses" || fn.Name.Name=="DefaultAnthropicMessages" || fn.Name.Name=="DefaultGoogleGenerativeAI" || fn.Name.Name=="DefaultEmbeddings" || fn.Name.Name=="DefaultRerank" || fn.Name.Name=="DefaultTranscriptions") && fn.Body != nil {
    for _,s := range fn.Body.List { if r, ok := s.(*ast.ReturnStmt); ok && len(r.Results)==1 { if _, ok := r.Results[0].(*ast.CompositeLit); ok { defaults[fn.Name.Name]=evaluate(r.Results[0]) } } }
   }
   if gen, ok := d.(*ast.GenDecl); ok { for _,s := range gen.Specs { ts, ok := s.(*ast.TypeSpec); if !ok || !strings.HasSuffix(ts.Name.Name,"Compat") { continue }; st, ok := ts.Type.(*ast.StructType); if !ok { continue }; mapping:=map[string]string{}
    for _,field := range st.Fields.List { if field.Tag == nil { continue }; tag,_:=strconv.Unquote(field.Tag.Value); start:=strings.Index(tag,"json:\""); if start>=0 { name:=strings.Split(tag[start+6:],"\"")[0]; name=strings.Split(name,",")[0]; mapping[field.Names[0].Name]=name } }; fields[ts.Name.Name]=mapping
   } }
  }
 }
 for path,f := range files {
  if !strings.Contains(filepath.ToSlash(path),"internal/models/providers/") { continue }
  ast.Inspect(f,func(n ast.Node) bool { a, ok := n.(*ast.AssignStmt); if ok { for i,l := range a.Lhs { if id, ok := l.(*ast.Ident); ok && id.Name=="rerankOnly" { constants[id.Name]=a.Rhs[i] } } }; return true })
  ast.Inspect(f,func(n ast.Node) bool {
   lit, ok := n.(*ast.CompositeLit); if !ok { return true }; name, ok := lit.Type.(*ast.Ident); if !ok || name.Name!="Definition" { return true }
   value := evaluate(lit).(map[string]any); delete(value,"Icon"); value["SourcePath"]="internal/models/providers/"+filepath.Base(path); out[value["ID"].(string)]=value; return false
  })
 }
 data, err := json.MarshalIndent(map[string]any{"version":1,"upstream":"3e8b0bfc80b845b2d4b2ed683994748741450a97","providers":out,"protocol_defaults":defaults,"compat_fields":fields},"","  "); if err != nil { panic(err) }
 if err := os.WriteFile(os.Args[2],append(data,'\n'),0644); err != nil { panic(err) }
 fmt.Printf("extracted %d fixed-source provider definitions\n",len(out))
}
