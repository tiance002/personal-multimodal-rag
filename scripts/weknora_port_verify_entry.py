"""Native development entry contracts. No legacy probe, deployment or real-provider mode."""
from __future__ import annotations
import argparse,hashlib,importlib,json,os,re,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

class EntryBlocked(ValueError):pass

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def checked(path,digest):
    if not path or not digest or not re.fullmatch('[0-9a-f]{64}',digest):raise EntryBlocked('EXPLICIT_INPUT_AND_SHA256_REQUIRED')
    file=Path(path).resolve()
    if not file.is_file() or sha(file)!=digest:raise EntryBlocked('INPUT_SHA256_MISMATCH')
    return file

def fresh_output(value):
    if not value:raise EntryBlocked('EXPLICIT_NEW_OUTPUT_REQUIRED')
    path=Path(value).resolve()
    if path.exists():raise EntryBlocked('OUTPUT_EXISTS_HISTORY_PRESERVED')
    return path

def isolated_read_environment(environment):
    parsed=urlsplit(environment.get('WEKNORA_PORT_TEST_DSN',''))
    if (parsed.scheme not in ('postgres','postgresql') or parsed.hostname!='127.0.0.1' or
        parsed.port!=56423 or parsed.path!='/weknora_python_port_20261013'):
        raise EntryBlocked('EXPLICIT_APPROVED_ISOLATED_DSN_REQUIRED')

def eval_command(args,environment=None):
    env=os.environ if environment is None else environment
    if args.mode not in ('verify-existing','execute-native'):raise EntryBlocked('EXPLICIT_NATIVE_EVAL_MODE_REQUIRED')
    if args.mode=='verify-existing' and not args.allow_isolated_read:raise EntryBlocked('ISOLATED_READ_AUTHORIZATION_REQUIRED')
    if args.mode=='execute-native' and not args.allow_isolated_mock_execution:raise EntryBlocked('ISOLATED_MOCK_EXECUTION_AUTHORIZATION_REQUIRED')
    manifest=checked(args.manifest,args.manifest_sha256)
    output=fresh_output(args.output)
    isolated_read_environment(env)
    cmd=[sys.executable,'-B']
    if args.mode=='verify-existing':
        if not args.results or len(args.results)!=len(args.result_sha256):raise EntryBlocked('EXPLICIT_RESULTS_AND_HASHES_REQUIRED')
        results=[checked(p,h) for p,h in zip(args.results,args.result_sha256)]
        cmd += [str(ROOT/'scripts/weknora_port_six_cohort_verify.py'),'--manifest',str(manifest),
            '--manifest-sha256',args.manifest_sha256,'--results',*[str(p) for p in results],
            '--result-sha256',*args.result_sha256,'--output',str(output)]
    else:
        if args.results or args.result_sha256:raise EntryBlocked('EXECUTE_MODE_CANNOT_REUSE_EXISTING_RESULTS')
        redis_id=env.get('WEKNORA_PORT_TEST_REDIS_RUN_ID','')
        if not re.fullmatch('[0-9a-f]{40}',redis_id):raise EntryBlocked('EXPLICIT_REDIS_RUN_ID_REQUIRED')
        cmd += [str(ROOT/'scripts/weknora_port_six_mock.py'),'--manifest',str(manifest),
            '--manifest-sha256',args.manifest_sha256,'--output',str(output),'--redis-run-id',redis_id,'--redis-db','15']
    return cmd,output

def write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')

def static_checks(root=ROOT):
    modules=['backend.weknora_port.development','backend.weknora_port.entrypoint','backend.weknora_port.worker','backend.weknora_port.initialize']
    imports={name:importlib.import_module(name).__file__ for name in modules}
    directory=root/'backend/weknora_port/storage/migrations'
    expected={json.loads(p.read_text(encoding='utf-8'))['generated_sha256'] for p in directory.glob('*.json')}
    sql={p.name:hashlib.sha256(p.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for p in directory.glob('*.sql')}
    if not expected or not expected<=set(sql.values()):raise EntryBlocked('MIGRATION_MANIFEST_CHECKSUM_MISMATCH')
    # 010 has no generated-source manifest; record its bytes without claiming a pin or applying it.
    return {'imports':imports,'migration_sql_sha256':sql,'manifest_checks':len(expected),
        'unmanifested_sql':[name for name,digest in sql.items() if digest not in expected],
        'schema_applied':False,'model_probe':'NOT_RUN','real_provider_calls':0}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    verify=sub.add_parser('verify');verify.add_argument('--output',required=True)
    evaluate=sub.add_parser('eval');evaluate.add_argument('--mode',required=True,choices=('schema','verify-existing','execute-native','retrieval','quality'))
    evaluate.add_argument('--dataset',default=str(ROOT/'evaluations/core.jsonl'))
    evaluate.add_argument('--manifest');evaluate.add_argument('--manifest-sha256');evaluate.add_argument('--output')
    evaluate.add_argument('--results',nargs='+',default=[]);evaluate.add_argument('--result-sha256',nargs='+',default=[])
    evaluate.add_argument('--allow-isolated-read',action='store_true');evaluate.add_argument('--allow-isolated-mock-execution',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.operation=='verify':
            output=fresh_output(args.output)
            temporary=output.parent/(output.name+'.pytest-tmp')
            if temporary.exists():raise EntryBlocked('TEST_OUTPUT_DIR_EXISTS_HISTORY_PRESERVED')
            checks=static_checks()
            tests=['tests/weknora_port/test_development_entry.py','tests/weknora_port/test_entrypoint_migration.py']
            command=[sys.executable,'-B','-m','pytest','--noconftest',*tests,'-q','-p','no:cacheprovider','--tb=short','--basetemp',str(temporary)]
            result=subprocess.run(command,cwd=ROOT)
            report={'gate':'NATIVE_DEVELOPMENT_ENTRY','scope':'STATIC_AND_TARGETED_OFFLINE_ONLY_NOT_M0_ACCEPTANCE',
                'status':'PASS' if result.returncode==0 else 'FAIL','checks':checks,'test_command':command,'test_exit':result.returncode,
                'database_services_started':False,'schema_applied':False,'model_probe':'NOT_RUN','real_provider_calls':0}
            write_new(output,report);print(json.dumps(report,ensure_ascii=False));return result.returncode
        if args.mode=='schema':
            output=fresh_output(args.output)
            from scripts.validate_eval import validate
            report=validate(Path(args.dataset));report.update(operation='schema-validation',executed_native=False,real_provider_calls=0)
            write_new(output,report);print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='PASS' else 1
        command,output=eval_command(args)
        output.parent.mkdir(parents=True,exist_ok=True)
        result=subprocess.run(command,cwd=ROOT)
        print(json.dumps({'operation':args.mode,'status':('VERIFIED_EXISTING' if args.mode=='verify-existing' else 'EXECUTED_NATIVE_MOCK') if result.returncode==0 else 'FAIL',
            'executed_native':args.mode=='execute-native','child_exit':result.returncode,'output':str(output),
            'model_quality':'NOT_RUN_SIMULATED_MODELS','real_provider_calls':0}))
        return result.returncode
    except (EntryBlocked,FileNotFoundError) as exc:
        print(json.dumps({'status':'BLOCKED','error_code':str(exc),'operation':args.operation,'real_provider_calls':0}));return 2

if __name__=='__main__':raise SystemExit(main())
