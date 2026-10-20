"""Offline safety checks of diagnostic tools; no HTTP/DB/Docker/provider calls."""
from __future__ import annotations
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import unittest
import uuid
import time
import subprocess
import sys
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent


def functions(filename, names, **namespace):
    tree=ast.parse((HERE/filename).read_text(encoding='utf-8'))
    body=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.name in names]
    namespace.update(Path=Path,json=json,os=os,uuid=uuid,hashlib=hashlib)
    exec(compile(ast.Module(body=body,type_ignores=[]),filename,'exec'),namespace)
    return namespace


class EnvelopeTests(unittest.TestCase):
    def test_memory_checkpoint_survives_native_sigterm_and_normal_exit(self):
        child=r'''
import ast,json,os,sys,time,uuid
from pathlib import Path
tree=ast.parse(Path(sys.argv[2]).read_text(encoding='utf-8'))
names={'canonical','json_default','safe_write','write_new','MemoryCheckpointWriter'}
nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
exec(compile(ast.Module(body=nodes,type_ignores=[]),'<offline-memory-checkpoint>','exec'))
writer=MemoryCheckpointWriter(Path(sys.argv[1]))
samples=[{'monotonic':time.monotonic(),'host_mem_available_kib':12345,'memory_peak':4567,'memory_max':1073741824}]
try:
 writer.checkpoint(samples)
 print('CHECKPOINT_READY',flush=True)
 if sys.argv[3]=='signal':time.sleep(60)
finally:writer.checkpoint(samples,finalized=True)
'''
        for mode in ('normal','signal'):
            with tempfile.TemporaryDirectory(dir=HERE) as directory:
                process=subprocess.Popen([sys.executable,'-c',child,directory,str(HERE/'s8_3_r_ecs_acceptance.py'),mode],
                                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(),'CHECKPOINT_READY')
                    if mode=='signal':process.terminate()
                    stdout,stderr=process.communicate(timeout=5)
                    self.assertEqual(stderr,'')
                    memory=json.loads((Path(directory)/'memory.json').read_text(encoding='utf-8'))
                    self.assertEqual(memory['sample_count'],1)
                    self.assertEqual(memory['host_mem_available_min_kib'],12345)
                    self.assertEqual(memory['checkpoint_status'],'periodic' if mode=='signal' else 'finalized')
                    journal=next(Path(directory).glob('memory-checkpoints-*.jsonl'))
                    self.assertGreaterEqual(len(journal.read_text(encoding='utf-8').splitlines()),1)
                finally:
                    if process.poll() is None:process.kill();process.communicate(timeout=5)

    def test_memory_checkpoint_preserves_prior_memory_artifact(self):
        ns=functions('s8_3_r_ecs_acceptance.py',{'canonical','json_default','safe_write','write_new','MemoryCheckpointWriter'})
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            path=Path(directory)/'memory.json';path.write_bytes(b'preserved prior receipt')
            writer=ns['MemoryCheckpointWriter'](directory)
            writer.checkpoint([{'host_mem_available_kib':10,'memory_peak':20,'memory_max':30}])
            self.assertEqual(path.read_bytes(),b'preserved prior receipt')
            self.assertNotEqual(writer.path,path)

    def test_search_nothing_fallback_requires_sse_and_no_persisted_facts(self):
        ns=functions('s8_3_r_ecs_acceptance.py',{'context_sources'})
        stage={'error_type':'SearchNothing','stages':[{'stage':'rerank','status':'started'}]}
        events=[{'response_type':'complete','data':{'is_fallback':True}}]
        messages=[{'role':'assistant','is_completed':True,'knowledge_references':[]}]
        self.assertEqual(ns['context_sources'](stage,'fixture-kb',events,messages),[])
        for bad_stage,bad_events,bad_messages in [
            ({**stage,'error_type':'ProviderError'},events,messages),
            (stage,[{'response_type':'complete','data':{'is_fallback':False}}],messages),
            (stage,events,[{**messages[0],'knowledge_references':[{'id':'fact'}]}]),
            ({**stage,'stages':[{'stage':'context','status':'completed'}]},events,messages)]:
            with self.assertRaisesRegex(RuntimeError,'FINAL_CONTEXT_MISSING'):
                ns['context_sources'](bad_stage,'fixture-kb',bad_events,bad_messages)

    def test_stop_failure_kills_and_requires_running_false(self):
        calls=[]
        def run(command,timeout):
            calls.append(command)
            return SimpleNamespace(returncode=1 if command[1]=='stop' else 0)
        ns=functions('remote_s8_3_r_case_controller.py',{'shutdown_candidate'},subprocess_run=run,
                     docker_inspect=lambda name:{'State':{'Running':False}})
        self.assertTrue(ns['shutdown_candidate']('s83-r-owned'))
        self.assertEqual([cmd[1] for cmd in calls],['stop','kill'])
        ns['docker_inspect']=lambda name:{'State':{'Running':True}}
        self.assertFalse(ns['shutdown_candidate']('s83-r-owned'))

    def test_sql_observer_preserves_result_error_and_method_identity(self):
        class Connection:
            async def execute(self,*args,**kwargs):
                if kwargs.get('fail'):raise ValueError('offline fixture')
                return args
        original=Connection.execute
        ns=functions('s8_3_r_ecs_acceptance.py',{'PassiveSQLObserver'},time=time)
        observer=ns['PassiveSQLObserver'](Connection);observer.install();observer.begin()
        async def exercise():
            self.assertEqual(await Connection().execute('opaque fixture'),('opaque fixture',))
            with self.assertRaises(ValueError):await Connection().execute(fail=True)
        coroutine=exercise()
        try:
            with self.assertRaises(StopIteration):coroutine.send(None)
        finally:coroutine.close();observer.restore()
        metrics=observer.finish()
        self.assertEqual((metrics['execute_calls'],metrics['completed_execute_calls'],metrics['failed_execute_calls']),(2,1,1))
        self.assertIs(Connection.execute,original)
        self.assertFalse(metrics['query_text_saved']);self.assertFalse(metrics['parameters_saved'])

    def test_parent_remaining_caps_and_exhaustion(self):
        ns=functions('remote_s8_3_r_case_controller.py',{'remaining_check'})
        cap=ns['remaining_check']
        self.assertEqual(cap(dict(question_posts=0,calls=0,held_tokens=0,unreported_usage_calls=0)),(12,100000))
        self.assertEqual(cap(dict(question_posts=10,calls=57,held_tokens=185000,unreported_usage_calls=0)),(3,15000))
        for field,value in [('calls',60),('held_tokens',200000),('question_posts',12),('unreported_usage_calls',1)]:
            row=dict(question_posts=0,calls=0,held_tokens=0,unreported_usage_calls=0);row[field]=value
            with self.assertRaisesRegex(RuntimeError,'INSUFFICIENT'):cap(row)

    def test_unknown_reserved_hold_is_not_zeroed(self):
        ns=functions('remote_s8_3_r_case_controller.py',{'reported_usage','child_totals'})
        totals=ns['child_totals']({'entries':[{'status':'UNKNOWN','reserved_tokens':7000,'held_tokens':7000,'usage':None}]})
        self.assertEqual(totals['held_tokens'],7000)
        self.assertEqual(totals['unknown'],1)
        self.assertEqual(totals['unreported_usage_calls'],1)

    def test_normal_factory_only_and_default_flags(self):
        tree=ast.parse((HERE/'s8_3_r_ecs_acceptance.py').read_text(encoding='utf-8'))
        factory=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_application')
        calls=[n for n in ast.walk(factory) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='create_application']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].args,[]);self.assertEqual(calls[0].keywords,[])
        ns=functions('remote_s8_3_r_case_controller.py',{'candidate_env'},
            PLAN={'model_binding_snapshot':{'query_understand_model_id':'q','effective_roles':{}}},
            RESOURCE={'database':'weknora_s8','postgres_system_id':'7698420805889187877','redis_run_id':'1'*40},
            EXEC=PurePosixPath('/opt/rag-s8/state/executions'),RUN=PurePosixPath('/opt/rag-s8/state/s8-3-r-20261020'))
        env=ns['candidate_env']({'case_id':'x','source_evidence_mode':'bounded_source_v1'},Path('/some-ledger'),'s83r_x_',str(uuid.uuid4()),Path('/logs'))
        self.assertEqual(env['WEKNORA_PORT_EXECUTION_ROOT'],'/opt/rag-s8/state/executions')
        self.assertEqual(env['WEKNORA_PORT_REDIS_PREFIX'],'weknora-python-s8:runtime')
        self.assertEqual(env['WEKNORA_PORT_SOURCE_EVIDENCE_MODE'],'off')
        self.assertEqual(env['WEKNORA_PORT_GRAPH_ENABLED'],'false')
        self.assertEqual(env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS'],'false')

    def test_passive_actual_wire_hash_mismatch_stops(self):
        ns=functions('s8_3_r_ecs_acceptance.py',{'canonical','json_default','sha','read_json','trace_observation','response_terminal_proof'})
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            path=Path(tmp);run=str(uuid.uuid4());sid=str(uuid.uuid4())
            body={'messages':[{'role':'user','content':'fixed fixture'}]}
            entry={'request_id':run,'capability':'chat','provider':'deepseek','model':'fixture','model_id':'fixture-id','status':'SETTLED','outcome':'RESPONSE_RECEIVED','usage':{'status':'REPORTED','total_tokens':2}}
            record={**entry,'body':body,'headers_saved':False}
            (path/'wire.request.json').write_text(json.dumps(record),encoding='utf-8')
            (path/'wire.response.json').write_text('{"complete":true,"headers_saved":false,"status_code":200}',encoding='utf-8')
            (path/'wire.response.body').write_bytes(b'offline response fixture')
            entry['wire_sha256']=ns['sha'](ns['canonical'](body))
            ledger={'entries':[entry],'evidence_dir':str(path)}
            stage={'run_id':run,'session_id':sid,'stages':[{'status':'completed','state':{'source_evidence_mode':'bounded_source_v1','graph_enabled':False,'evidence_trace':{}}}]}
            case={'source_evidence_mode':'bounded_source_v1'}
            self.assertEqual(ns['trace_observation'](stage,ledger,case,sid,run)['status'],'PASS')
            entry['wire_sha256']='f'*64
            with self.assertRaisesRegex(RuntimeError,'HASH_MISMATCH'):ns['trace_observation'](stage,ledger,case,sid,run)

    def test_terminal_sse_requires_done_stop_and_exact_settled_usage(self):
        ns=functions('s8_3_r_ecs_acceptance.py',{'response_terminal_proof','sha'})
        record={'capability':'chat','body':{'stream':True}}
        usage={'prompt_tokens':1592,'completion_tokens':66,'total_tokens':1658}
        entry={'status':'SETTLED','outcome':'RESPONSE_RECEIVED','usage':{'status':'REPORTED',**usage}}
        response={'complete':False,'status_code':200}
        raw=('data: '+json.dumps({'choices':[{'finish_reason':'stop'}],'usage':usage})+'\n\ndata: [DONE]\n\n').encode()
        proof=ns['response_terminal_proof'](record,response,raw,entry)
        self.assertTrue(proof['protocol_terminal_proved']);self.assertFalse(proof['transport_eof_observed'])
        bads=[raw.replace(b'data: [DONE]\n\n',b''),raw.replace(b'"stop"',b'"length"'),raw.replace(b'1658',b'1659'),b'data: malformed\n\ndata: [DONE]\n\n']
        for bad in bads:
            with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):ns['response_terminal_proof'](record,response,bad,entry)
        with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):ns['response_terminal_proof'](record,response,raw,{**entry,'status':'UNKNOWN'})

    def test_one_session_admission_durable_no_retry(self):
        ns=functions('s8_3_r_ecs_acceptance.py',{'canonical','json_default','sha','write_new','api_payload','source_path_allowed','SingleQuestionAdmission'},SQL_OBSERVER=None)
        async def exercise(directory):
            case={'case_id':'fixture','knowledge_base_id':'fixture-kb','source_evidence_mode':'bounded_source_v1','question':'frozen'}
            calls=[]
            async def inner(scope,receive,send):
                calls.append(scope['path'])
                await receive()
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'{"data":{"id":"fixture-session"}}'})
            app=ns['SingleQuestionAdmission'](inner,case,Path(directory),'request-id','fixture-title')
            async def invoke(path,body):
                sent=[]
                async def receive():return {'type':'http.request','body':json.dumps(body).encode()}
                async def send(item):sent.append(item)
                await app({'type':'http','method':'POST','path':path},receive,send)
                return sent[0]['status']
            payload={'knowledge_base_scope':['fixture-kb'],'document_scope':[],'title':'fixture-title','source_evidence_mode':'bounded_source_v1'}
            self.assertEqual(await invoke('/api/v1/sessions',payload),200)
            self.assertEqual(await invoke('/api/v1/sessions',payload),409)
            question=ns['api_payload'](case,'request-id')
            self.assertEqual(await invoke('/api/v1/sessions/fixture-session/messages',question),200)
            self.assertEqual(await invoke('/api/v1/sessions/fixture-session/messages',question),409)
            self.assertEqual(len(calls),2)
            self.assertTrue((Path(directory)/'question-post-claim.json').is_file())
        # This coroutine awaits only immediately completing in-memory functions.
        # Drive it directly to keep this offline test independent of Windows
        # asyncio's loopback socketpair initialization in restricted sandboxes.
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            coroutine=exercise(tmp)
            try:
                with self.assertRaises(StopIteration):coroutine.send(None)
            finally:coroutine.close()


if __name__=='__main__':unittest.main(verbosity=2)
