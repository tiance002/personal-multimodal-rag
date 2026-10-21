"""Static retirement review only; never deletes files or runs legacy entrypoints."""
from pathlib import Path
import ast,json,hashlib,subprocess
ROOT=Path(__file__).resolve().parents[1]
git=r'C:\Program Files\Git\cmd\git.exe'
tracked=subprocess.check_output([git,'ls-files'],cwd=ROOT,text=True).splitlines()
modules={p[:-3].replace('/','.').removesuffix('.__init__'):p for p in tracked if p.endswith('.py')}
core=['query_router','rule_router_v1','router_feature_extraction','quick_chain','langchain_agent','knowledge_gateway','knowledge_tools','agent_ports','execution_routing','answer_service','answer_validation','answer_hardening','rag_validation_scope','static_pair_validation','retrieval','retrieval_merge','retrieval_policy','retrieval_profile','retrieval_provenance','context_builder','context_expansion','context_window','query_understand','query_expansion','follow_up','evidence_quality','evidence_accumulator','question_checklist','quality','citations','llm_rerank']
candidates={f'backend/app/application/{n}.py':'old RAG/route/context/validator' for n in core}
candidates.update({p:'old provider/entry or grant-specific probe' for p in ['backend/app/main.py','backend/app/workers/ingestion.py','backend/app/adapters/redis_stream.py','backend/app/adapters/models/cloud.py','backend/app/adapters/models/factory.py','scripts/ordinary_verification_fence.py','scripts/smoke_ordinary_output_capacity.py','scripts/model_probe.py','scripts/smoke_ordinary_deepseek_tls.py','scripts/smoke_service_capacity_linux.py','scripts/evaluate_rag_quality.py','scripts/replay_answer_hardening.py','scripts/replay_p5_router.py','frontend/src/app/App.tsx']})
missing=sorted(set(candidates)-set(tracked));candidates={p:v for p,v in candidates.items() if p in tracked}
callers={p:[] for p in candidates};errors=[]
for path in tracked:
 if path.endswith('.py') and not path.startswith(('docs/','var/')):
  try:tree=ast.parse((ROOT/path).read_text(encoding='utf-8-sig'))
  except (UnicodeError,SyntaxError,OSError) as exc:errors.append({'file':path,'error':type(exc).__name__});continue
  package=path[:-3].replace('/','.').split('.')[:-1]
  if path.endswith('/__init__.py'):package=path[:-12].replace('/','.').split('.')
  for node in ast.walk(tree):
   names=[]
   if isinstance(node,ast.Import):names=[a.name for a in node.names]
   elif isinstance(node,ast.ImportFrom):
    prefix=package[:len(package)-node.level+1] if node.level else []
    module='.'.join(prefix+([node.module] if node.module else []))
    names=[module]+[module+'.'+a.name for a in node.names]
   for name in names:
    target=modules.get(name)
    if target in callers:callers[target].append({'file':path,'line':node.lineno,'kind':'AST_IMPORT','module':name})
 # Explicit command/module/frontend references outside historical documents.
 if path.startswith(('backend/weknora_port/','deploy/','frontend/','scripts/','eval_center/')) or path in ('pyproject.toml','Makefile'):
  if path.startswith(('docs/',)) or path.endswith(('.png','.jpg','.pdf','.lock')):continue
  try:lines=(ROOT/path).read_text(encoding='utf-8-sig').splitlines()
  except (UnicodeError,OSError):continue
  for no,line in enumerate(lines,1):
   for target in candidates:
    dotted=target[:-3].replace('/','.') if target.endswith('.py') else ''
    tokens=[target]+([dotted] if dotted else [])
    if target=='frontend/src/app/App.tsx':tokens+=['./app/App']
    if any(token in line for token in tokens):callers[target].append({'file':path,'line':no,'kind':'EXPLICIT_TEXT_REFERENCE','text':line.strip()[:240]})
rows=[]
for path,reason in sorted(candidates.items()):
 refs=sorted({json.dumps(r,sort_keys=True):r for r in callers[path]}.values(),key=lambda r:(r['file'],r['line'],r['kind']))
 rows.append({'path':path,'sha256':hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),'reason':reason,'status':'REVIEW_ONLY_DO_NOT_DELETE','current_callers':refs,'external_callers':[r for r in refs if r['file'] not in candidates],'post_delete_checks':['affected import/contract tests and callers in this row','new wheel member/import guard','new HTTP upload/index/QA/reference/history/SSE boundary tests']})
protected=[p for p in tracked if p.startswith('backend/app/') and any(token in p.lower() for token in ('budget','ledger','run_','session_attempt','usage','grant','schema'))]
report={'kind':'EXACT_PATH_STATIC_RETIREMENT_REVIEW_NOT_DELETE_AUTHORIZATION','source_commit':subprocess.check_output([git,'rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'candidate_count':len(rows),'candidates':rows,'requested_paths_not_present':missing,'parse_errors':errors,'protected_support_code_review_hold':protected,'preserve_data':['all ledger/UNKNOWN/Run/Attempt/Gold/original source/version/history evidence/backups','all migration files and old DB volumes','backend/weknora_port and eval_center/weknora*'],'limits':'AST and explicit tracked-text scan, not dynamic reachability proof. External shell/service configs and untracked files not scanned. Existing historical documents/evidence are not callers to delete. No command or file deletion performed.'}
out=ROOT/'docs/weknora-port/evidence/P8_RETIREMENT_REVIEW_20261015';out.mkdir(exist_ok=True)
(out/'.gitattributes').write_bytes(b'* -text -whitespace\n')
(out/'inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
md=['# P8 exact retirement candidates (review only)','',f'Frozen source before inventory: `{report["source_commit"]}`. {len(rows)} exact files. No deletion authorization or deletion performed. Historical records are retained.','', 'AST import and explicit text callers are listed per file in evidence/P8_RETIREMENT_REVIEW_20261015/inventory.json, with line numbers and hashes. This is not a dynamic reachability proof; an empty caller list is not deletion approval.','', '| Exact file | Current caller locations | Outside candidate set |','|---|---:|---:|']
for row in rows:md.append(f'| `{row["path"]}` | {len(row["current_callers"])} | {len(row["external_callers"])} |')
md+=['','## Required caller changes before any later deletion','','- `frontend/src/main.tsx` still explicitly lazy-imports legacy App for VITE_RAG_IMPLEMENTATION=legacy. Remove that branch only with Owner approval; build/typecheck must verify new UI remains.','- `deploy/legacy` intentionally names old API/worker/probe paths. Keep historical configs as marked artifacts or explicitly retire them with the approved later change; do not treat their current references as default-new-chain dependencies.','- Existing backend/tests and eval_center/scripts still import old modules. Each outside-candidate caller must be ported, explicitly retired or retained with its dependency; do not mass-delete tests to obtain a passing suite.','- Ledger/cost/Run/Attempt/grant/schema support code listed under protected_support_code_review_hold is excluded from candidates. Retain audit/readback and migration capability until a separately reviewed replacement exists.','', '## Validation required after a separately approved deletion','','1. Resolve every external caller in the JSON inventory, check affected import/API/SSE contracts and test collection; do not run paid legacy probes.','2. Rebuild from exact final Git source; wheel excludes old backend.app and loads all production modules from installed paths. Import new API/worker/init console entries.','3. Run affected offline invariants plus isolated upload/parser/index/QA/reference/history/memory/image-worker and cancellation/replay/idempotency checks, using synthetic model transport.','4. React typecheck/build and actual new-chain UI/HTTP route contract checks; verify legacy selector removal explicitly if approved.','5. Preserve hashes of all historical receipts/Gold/protected files and confirm no schema/volume/UNKNOWN/data deletions. Existing service/cutover and real-provider checks remain separately approved.','',f'Python parse errors: {len(errors)}; absent requested paths: {missing}. No blanket directory deletion proposed.']
(ROOT/'docs/weknora-port/P8_RETIREMENT_REVIEW_20261015.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
print(json.dumps({'candidates':len(rows),'parse_errors':errors,'missing':missing,'protected_code_files':len(protected),'new_runtime_import_callers':sum(r['file'].startswith('backend/weknora_port/') and r['kind']=='AST_IMPORT' for row in rows for r in row['current_callers'])},indent=2))
