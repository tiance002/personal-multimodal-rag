"""Independent-review counterexample regressions, all SIMULATED/offline."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('base_native_release_tests',ROOT/'backend/tests/test_native_release.py')
original=importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
tool=original.tool


class ReviewRegressionTests(original.NativeReleaseTests):
    def setUp(self):
        super().setUp()
        config=self.root/'explicit docker config'; config.mkdir()
        self.fields.update(docker_endpoint='npipe:////./pipe/explicit_engine',docker_config_root=str(config),
                           db_container_id='a'*64,db_system_identifier='1234567890123456789',database_oid=16384)

    def backup_fixture(self):
        fixture=self.source/'backend/app/application/backup.py'; fixture.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'backend/app/application/backup.py',fixture)

    def test_review_docker_inspect_psql_dump_share_explicit_environment_and_id(self):
        t=self.selected(); url='postgresql://fixture:SIMULATED_SECRET@127.0.0.1:25438/'+t['database_name']
        calls=[]
        def fake_run(command,**kwargs):
            calls.append((command,dict(kwargs['env'])))
            if 'inspect' in command:
                info={'Id':t['db_container_id'],'Name':'/'+t['db_container'],
                      'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'25438'}]}}
                return types.SimpleNamespace(returncode=0,stdout=json.dumps(info).encode())
            if 'psql' in command:
                info={'database':t['database_name'],'system_identifier':t['db_system_identifier'],'database_oid':t['database_oid']}
                return types.SimpleNamespace(returncode=0,stdout=json.dumps(info).encode())
            kwargs['stdout'].write(b'SIMULATED dump from explicitly pinned daemon')
            return types.SimpleNamespace(returncode=0)
        parent={'DOCKER_HOST':'SIMULATED_A','DOCKER_CONTEXT':'SIMULATED_B','USERPROFILE':'D:/other config',
                'SYSTEMROOT':'C:/Windows','DEEPSEEK_API_KEY':'DO_NOT_READ'}
        with patch.object(tool.os,'environ',parent),patch.object(tool.subprocess,'run',side_effect=fake_run):
            tool.check_container_mapping(t,url)
            tool.check_container_database_identity(t,url)
            tool.run_pg('pg_dump',['--format=custom'],t,url,output=self.backups/'synthetic.dump')
        self.assertEqual(len(calls),3)
        self.assertTrue(all(env==calls[0][1] for _,env in calls))
        for command,env in calls:
            self.assertEqual(command[command.index('--host')+1],t['docker_endpoint'])
            self.assertEqual(command[command.index('--config')+1],t['docker_config_root'])
            self.assertNotIn('DOCKER_HOST',env); self.assertNotIn('DOCKER_CONTEXT',env)
            self.assertNotIn('USERPROFILE',env); self.assertNotIn('DEEPSEEK_API_KEY',env)
            self.assertNotIn('SIMULATED_SECRET',str(command))
            if 'exec' in command: self.assertIn(t['db_container_id'],command)

    def test_review_wrong_container_database_identity_rejected_before_transfer(self):
        t=self.selected(); url='postgresql://u:synthetic@127.0.0.1:25438/'+t['database_name']
        for field,value in [('system_identifier','9999999999999999999'),('database_oid',100),('database','wrong_db')]:
            info={'database':t['database_name'],'system_identifier':t['db_system_identifier'],'database_oid':t['database_oid']}
            info[field]=value
            with patch.object(tool.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout=json.dumps(info).encode())):
                with self.assertRaises(tool.ReleaseError): tool.check_container_database_identity(t,url)

    def test_review_missing_explicit_docker_or_database_identity_is_rejected(self):
        for field in ['docker_endpoint','docker_config_root','db_container_id','db_system_identifier','database_oid']:
            values=dict(self.fields); del values[field]
            with self.assertRaises(tool.ReleaseError): tool.select_target(values)
        for endpoint in ['tcp://remote:2375','', 'default']:
            with self.assertRaises(tool.ReleaseError): tool.select_target(dict(self.fields,docker_endpoint=endpoint))

    def test_review_manifest_6000_entry_json_roundtrip_and_capacity(self):
        # Serialize the review's 6000-row shape; no 6000 real objects or corpus involved.
        inventory=[{'path':'objects/ab/'+format(i,'064x'),'size':9,'sha256':'b'*64} for i in range(6000)]
        value={'manifest_version':1,'storage':inventory}
        p=self.backups/'large-manifest.json'
        tool.write_new_json(p,value,max_bytes=tool.MAX_BACKUP_MANIFEST)
        self.assertGreater(p.stat().st_size,1024*1024)
        loaded=tool.read_json(p,max_bytes=tool.MAX_BACKUP_MANIFEST)
        self.assertEqual(loaded,value)
        with self.assertRaises(tool.ReleaseError): tool.read_json(p)  # target JSON is intentionally smaller.
        too_big=self.backups/'too-big.json'
        with self.assertRaises(tool.ReleaseError): tool.write_new_json(too_big,value,max_bytes=100)
        self.assertFalse(too_big.exists())

    def test_review_backup_self_reader_precedes_success_and_overflow_leaves_incomplete(self):
        self.backup_fixture(); t=self.selected()
        def fake_dump(program,flags,target,url,**kwargs): Path(kwargs['output']).write_bytes(b'SIMULATED dump')
        mocks=[patch.object(tool,'runtime_dsn',return_value='SIMULATED'),patch.object(tool,'check_database'),
               patch.object(tool,'check_container_mapping'),patch.object(tool,'check_container_database_identity'),
               patch.object(tool,'run_pg',side_effect=fake_dump)]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in mocks: stack.enter_context(p)
            with patch.object(tool,'verified_backup',wraps=tool.verified_backup) as reader:
                result=tool.execute_backup(t,self.backups/'complete',True)
                self.assertGreaterEqual(reader.call_count,2)
                self.assertEqual(reader.call_args_list[0].kwargs['manifest_name'],'manifest.pending.json')
            self.assertEqual(result['status'],'BACKUP_FILES_VERIFIED_RESTORE_NOT_RUN')
            failed=self.backups/'capacity-failure'
            with patch.object(tool,'MAX_BACKUP_MANIFEST',100):
                with self.assertRaises(tool.ReleaseError): tool.execute_backup(t,failed,True)
            self.assertTrue((failed/'failure.json').is_file())
            self.assertFalse((failed/'manifest.json').exists())
            with self.assertRaises(tool.ReleaseError): tool.verified_backup(t,failed)

    def test_review_gate_plan_forwards_both_targets_no_execution(self):
        t=self.selected(); a=self.root/'target.json'; a.write_text(json.dumps(t))
        restored=tool.select_target(dict(self.fields,purpose='restore',database_name='knowledge_restore_20261004',
                         database_oid=16385,storage_root=str(self.root/'new restored storage')))
        b=self.root/'restore-target.json'; b.write_text(json.dumps(restored))
        args=types.SimpleNamespace(target_file=a,restore_target_file=b,
                   restore_database_name=restored['database_name'],restore_storage_root=restored['storage_root'],
                   output_dir=str(self.backups/'future backup'))
        with patch.object(tool,'runtime_dsn',side_effect=AssertionError('no secret read')), patch.object(tool,'execute_backup',side_effect=AssertionError('no backup')), patch.object(tool,'execute_restore',side_effect=AssertionError('no restore')):
            plan=tool.native_gate_plan(t,args)
        self.assertEqual(plan['status'],'NATIVE_GATE_PLAN_REAL_ACCEPTANCE_NOT_RUN')
        self.assertIn(str(a),plan['commands']['backup']); self.assertIn(str(b),plan['commands']['restore'])
        self.assertIn(restored['database_name'],plan['commands']['restore'])
        self.assertIn(restored['storage_root'],plan['commands']['restore'])
        self.assertNotIn('--execute',plan['commands']['backup'])
        self.assertEqual(plan['execution_confirmations']['restore'],restored['target_id'])

    def test_review_host_identity_mismatch_is_refused(self):
        t=self.selected()
        tool.validate_database_identity(t,{'database':t['database_name'],'system_identifier':t['db_system_identifier'],'database_oid':t['database_oid']})
        with self.assertRaises(tool.ReleaseError):
            tool.validate_database_identity(t,{'database':t['database_name'],'system_identifier':'999','database_oid':t['database_oid']})


if __name__=='__main__':
    unittest.main(verbosity=2)
