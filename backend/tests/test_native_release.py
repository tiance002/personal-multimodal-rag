"""Offline safety tests: fixtures and mock execution only; no real services."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import shutil
import unittest
from unittest.mock import patch, Mock

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'native_release.py'
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location('native_release', SCRIPT)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
else:
    tool = None


@unittest.skipUnless(os.getenv('RAG_NATIVE_TEST_TMP'), 'Explicit disposable test root required')
class NativeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['RAG_NATIVE_TEST_TMP'])
        self.root = Path(self.tmp.name)
        self.source = self.root / 'project with spaces'
        for rel in ['backend/app', 'alembic/versions', 'scripts', 'frontend/dist', 'frontend/node_modules/vite/bin']:
            (self.source / rel).mkdir(parents=True, exist_ok=True)
        (self.source / 'pyproject.toml').write_text('[project]\ndependencies=["fastapi==0.141.1","PyMuPDF==1.26.4","openpyxl==3.1.5","defusedxml==0.7.1","Pillow==12.3.0"]\n')
        (self.source / 'backend/app/main.py').write_text('# source identity\n')
        (self.source / 'scripts/start_backend.py').write_text('# launcher\n')
        (self.source / 'scripts/native_release_vite.config.mjs').write_text('# preview\n')
        (self.source / 'alembic/versions/0013_message_run_link.py').write_text('revision="0013_message_run_link"\ndown_revision=None\n')
        (self.source / 'frontend/dist/index.html').write_text('<html>frozen UI</html>')
        (self.source / 'frontend/package-lock.json').write_text('{"lockfileVersion":3}')
        (self.source / 'frontend/node_modules/vite/bin/vite.js').write_text('// vite')
        self.storage = self.root / 'selected storage'
        (self.storage / 'objects').mkdir(parents=True)
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        self.docker_config=self.root/'selected docker config'; self.docker_config.mkdir()
        self.tessdata = self.root / 'tessdata'
        self.tessdata.mkdir()
        for lang in ['eng', 'chi_sim']:
            (self.tessdata / (lang + '.traineddata')).write_bytes(b'fixture language')
        self.python = self.runtime('main venv', {'fastapi':'0.141.1','PyMuPDF':'1.26.4','openpyxl':'3.1.5','defusedxml':'0.7.1','Pillow':'12.3.0'})
        self.native = self.runtime('native venv', {'beautifulsoup4':'4.15.0','python-docx':'1.2.0','defusedxml':'0.7.1','lxml':'6.0.0','soupsieve':'2.8'})
        self.node = self.executable('node.exe')
        self.docker = self.executable('docker.exe')
        self.fields = dict(source_root=str(self.source), python=str(self.python), native_python=str(self.native),
            node=str(self.node), docker=str(self.docker), db_container='explicit-existing-db',
            docker_endpoint='npipe:////./pipe/explicit_engine',docker_config_root=str(self.docker_config),
            db_container_id='a'*64,db_system_identifier='1234567890123456789',database_oid=16384,
            tessdata_dir=str(self.tessdata), database_name='knowledge_release_20261004', db_port=25438,
            storage_root=str(self.storage), backup_root=str(self.backups), api_port=18193, ui_port=14193,
            purpose='release', migration_revision='0013_message_run_link')

    def tearDown(self):
        self.tmp.cleanup()

    def executable(self, name):
        path = self.root / name
        path.write_bytes(b'fixture executable, never launched')
        return path

    def runtime(self, name, versions):
        root = self.root / name
        (root / 'Scripts').mkdir(parents=True)
        (root / 'Scripts/python.exe').write_bytes(b'fixture never launched')
        (root / 'pyvenv.cfg').write_text('version = 3.11.9\n')
        site = root / 'Lib/site-packages'
        for dist, version in versions.items():
            folder = site / (dist.replace('-', '_') + '-' + version + '.dist-info')
            folder.mkdir(parents=True)
            (folder / 'METADATA').write_text('Name: '+dist+'\nVersion: '+version+'\n')
        return root / 'Scripts/python.exe'

    def selected(self):
        return tool.select_target(self.fields)

    def test_select_is_read_only_and_binds_explicit_pair(self):
        before = sorted(str(x) for x in self.root.rglob('*'))
        target = self.selected()
        self.assertEqual(target['database_name'], self.fields['database_name'])
        self.assertEqual(target['storage_root'], str(self.storage.resolve()))
        self.assertEqual(before, sorted(str(x) for x in self.root.rglob('*')))

    def test_missing_explicit_fields_rejected(self):
        for field in ['python','native_python','database_name','storage_root','backup_root','tessdata_dir','db_port']:
            values = dict(self.fields)
            del values[field]
            with self.assertRaises(tool.ReleaseError): tool.select_target(values)

    def test_relative_storage_and_default_release_db_rejected(self):
        for values in [dict(self.fields, storage_root='var/storage'), dict(self.fields, database_name='rag'),
                       dict(self.fields, database_name='rag_acceptance_integration01_20261001_100522')]:
            with self.assertRaises(tool.ReleaseError): tool.select_target(values)

    def test_test_db_requires_explicit_acceptance_purpose(self):
        t = tool.select_target(dict(self.fields, database_name='rag_acceptance_example', purpose='acceptance'))
        self.assertEqual(t['purpose'], 'acceptance')

    def test_wrong_db_or_storage_rejected(self):
        t = self.selected()
        for name, root in [('wrong_db', str(self.storage)), (t['database_name'], str(self.backups))]:
            with self.assertRaises(tool.ReleaseError): tool.assert_selection(t, name, root)

    def test_target_tamper_and_secret_fields_rejected_without_echo(self):
        t = self.selected()
        t['password'] = 'DO_NOT_ECHO_SECRET'
        with self.assertRaises(tool.ReleaseError) as ctx: tool.validate_target(t)
        self.assertNotIn('DO_NOT_ECHO_SECRET', str(ctx.exception))
        t = self.selected(); t['storage_root'] = str(self.backups)
        with self.assertRaises(tool.ReleaseError): tool.validate_target(t)

    def test_parser_resource_and_version_preflight(self):
        t = self.selected(); info = tool.offline_preflight(t)
        self.assertEqual(info['status'], 'STATIC_PASS_RUNTIME_NOT_RUN')
        self.assertEqual(info['formats'], ['TXT','MD','PDF','HTML','DOCX','XLSX'])
        self.assertFalse(info['automatic_doc'])
        (self.tessdata/'chi_sim.traineddata').unlink()
        with self.assertRaises(tool.ReleaseError): tool.offline_preflight(t)

    def test_wrong_native_version_rejected(self):
        t = self.selected()
        file = next((self.native.parent.parent/'Lib/site-packages').glob('python_docx-*/METADATA'))
        file.write_text('Name: python-docx\nVersion: 0.0.1\n')
        with self.assertRaises(tool.ReleaseError): tool.offline_preflight(t)

    def test_source_drift_rejected(self):
        t = self.selected()
        (self.source/'backend/app/main.py').write_text('# newer gate\n')
        with self.assertRaises(tool.ReleaseError): tool.offline_preflight(t)

    def test_command_uses_argument_lists_spaces_loopback_no_env(self):
        t = self.selected(); commands = tool.start_commands(t)
        self.assertIn(str(self.python), commands['api'])
        self.assertIn('--no-env', commands['api'])
        self.assertIn('-I', commands['api'])
        self.assertEqual(commands['ui'][commands['ui'].index('--host')+1], '127.0.0.1')
        self.assertIn('--strictPort', commands['ui'])
        self.assertNotIn('0.0.0.0', json.dumps(commands))

    def test_env_is_allowlisted_secrets_not_forwarded(self):
        t = self.selected()
        env = tool.child_environment(t, {'DEEPSEEK_API_KEY':'DO_NOT_ECHO_SECRET','VITE_PASSWORD':'secret','RAG_HOST':'0.0.0.0','SYSTEMROOT':'C:/Windows'}, database_url='postgresql://u:p@127.0.0.1:25438/'+t['database_name'])
        self.assertNotIn('DEEPSEEK_API_KEY', env)
        self.assertNotIn('VITE_PASSWORD', env)
        self.assertEqual(env['RAG_HOST'], '127.0.0.1')
        self.assertEqual(env['RAG_CLOUD_ENABLED'], 'false')
        self.assertEqual(env['RAG_NATIVE_API_PORT'], '18193')

    def test_dsn_mismatch_and_remote_host_rejected_safe(self):
        t = self.selected()
        for url in ['postgresql://u:DO_NOT_ECHO_SECRET@127.0.0.1:25438/wrong',
                    'postgresql://u:DO_NOT_ECHO_SECRET@remote:25438/'+t['database_name'],
                    'postgresql://u:DO_NOT_ECHO_SECRET@127.0.0.1:5432/'+t['database_name']]:
            with self.assertRaises(tool.ReleaseError) as ctx: tool.validate_dsn(t, url)
            self.assertNotIn('DO_NOT_ECHO_SECRET', str(ctx.exception))

    def test_default_dry_run_never_spawns_reads_runtime_env_or_checks_ports(self):
        t = self.selected(); file = self.root/'target.json'; file.write_text(json.dumps(t))
        with patch.object(tool, 'execute_start', side_effect=AssertionError('must not run')), patch.object(tool, 'runtime_dsn', side_effect=AssertionError('must not read env')), patch.object(tool, 'check_port', side_effect=AssertionError('must not bind')):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = tool.main(['start','--target-file',str(file),'--database-name',t['database_name'],'--storage-root',t['storage_root'],'--component','ui'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())['status'], 'DRY_RUN')

    def test_missing_cli_args_and_unknown_secret_not_echoed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main(['select','--password','DO_NOT_ECHO_SECRET'])
        self.assertEqual(code, 2)
        self.assertNotIn('DO_NOT_ECHO_SECRET', out.getvalue())

    def test_backup_existing_directory_or_overlap_rejected(self):
        t = self.selected()
        for destination in [self.backups, self.storage/'backup', self.source/'backup']:
            with self.assertRaises(tool.ReleaseError): tool.backup_destination(t, str(destination))
        good = self.backups/'new backup with spaces'
        self.assertEqual(tool.backup_destination(t,str(good)),good)
        self.assertFalse(good.exists())

    def test_execute_requires_exact_confirmation(self):
        t = self.selected()
        with self.assertRaises(tool.ReleaseError): tool.require_confirmation(t,'not-matching')
        tool.require_confirmation(t,t['target_id'])

    def test_port_occupied_blocks_no_fallback(self):
        with patch.object(tool.socket,'socket') as ctor:
            ctor.return_value.__enter__.return_value.bind.side_effect = OSError('secret detail')
            with self.assertRaises(tool.ReleaseError) as ctx: tool.check_port(14193)
            self.assertEqual(str(ctx.exception),'LOOPBACK_PORT_UNAVAILABLE')

    def test_db_storage_object_pair_validation(self):
        raw=b'original'; digest=hashlib.sha256(raw).hexdigest(); key='objects/'+digest[:2]+'/'+digest
        path=self.storage/key; path.parent.mkdir(); path.write_bytes(raw)
        tool.validate_objects(self.storage,[(key,digest)])
        for rows in [[(key,'0'*64)],[('objects/../secret',digest)],[('objects/00/missing',digest)]]:
            with self.assertRaises(tool.ReleaseError): tool.validate_objects(self.storage,rows)

    def test_backup_requires_quiescent_confirmation_before_side_effect(self):
        t=self.selected(); dest=self.backups/'new'
        with patch.object(tool,'runtime_dsn',side_effect=AssertionError('must not read env')):
            with self.assertRaises(tool.ReleaseError): tool.execute_backup(t,dest,False)
        self.assertFalse(dest.exists())

    def test_restore_target_requires_new_storage_and_different_db(self):
        original=self.selected()
        new=tool.select_target(dict(self.fields,purpose='restore',database_name='knowledge_restore_20261004',database_oid=16385,storage_root=str(self.root/'new restored storage')))
        tool.validate_restore_target(new,original)
        with self.assertRaises(tool.ReleaseError): tool.validate_restore_target(original,original)
        (Path(new['storage_root'])).mkdir()
        with self.assertRaises(tool.ReleaseError): tool.validate_restore_target(new,original)

    def test_restore_nonempty_database_rejected(self):
        connection=Mock(); connection.execute.return_value.fetchone.return_value=(1,)
        with self.assertRaises(tool.ReleaseError): tool.require_empty_database(connection)

    def test_no_secret_traceback_on_runtime_failure(self):
        t=self.selected(); file=self.root/'target.json'; file.write_text(json.dumps(t))
        out=io.StringIO()
        with patch.object(tool,'require_execution_python'), patch.object(tool,'execute_start',side_effect=RuntimeError('password=DO_NOT_ECHO_SECRET')):
            with contextlib.redirect_stdout(out):
                code=tool.main(['start','--target-file',str(file),'--database-name',t['database_name'],'--storage-root',t['storage_root'],'--component','api','--execute','--confirm-target',t['target_id']])
        self.assertEqual(code,1)
        self.assertNotIn('DO_NOT_ECHO_SECRET',out.getvalue())
        self.assertNotIn('Traceback',out.getvalue())

    def test_launcher_no_env_skips_loader_and_forces_selected_loopback(self):
        launcher_file=SCRIPT.parent/'start_backend.py'
        spec=importlib.util.spec_from_file_location('release_launcher_test',launcher_file)
        launcher=importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        settings=types.SimpleNamespace(host='127.0.0.1',port=18193)
        uvicorn=Mock(); loader=Mock(side_effect=AssertionError('real env must not be loaded'))
        modules={'backend.app.env_loader':types.SimpleNamespace(load_backend_env=loader),
                 'backend.app.config':types.SimpleNamespace(Settings=types.SimpleNamespace(from_env=lambda:settings)),
                 'uvicorn':uvicorn}
        with patch.dict('sys.modules',modules),patch('os.chdir'):
            launcher.main(['--no-env'])
        loader.assert_not_called()
        uvicorn.run.assert_called_once_with('backend.app.main:app',host='127.0.0.1',port=18193)

    def test_vite_config_disables_env_and_has_no_wildcard_host(self):
        source=(SCRIPT.parent/'native_release_vite.config.mjs').read_text(encoding='utf-8-sig')
        self.assertIn('envDir: false',source)
        self.assertIn("host: '127.0.0.1'",source)
        self.assertIn('strictPort: true',source)
        self.assertNotIn('0.0.0.0',source)

    def test_owned_process_cleanup_uses_only_created_handle(self):
        process=Mock(); process.wait.side_effect=[KeyboardInterrupt(),0]; process.poll.return_value=None
        with patch.object(tool.subprocess,'Popen',return_value=process) as ctor:
            with self.assertRaises(KeyboardInterrupt): tool.owned_foreground(['explicit.exe'],str(self.source),{})
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        self.assertFalse(ctor.call_args.kwargs['shell'])

    def test_docker_container_mapping_wrong_port_rejected(self):
        t=self.selected()
        result=types.SimpleNamespace(returncode=0,stdout=json.dumps({'Id':t['db_container_id'],'Name':'/'+t['db_container'],'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'9999'}]}}).encode())
        with patch.object(tool.subprocess,'run',return_value=result):
            with self.assertRaises(tool.ReleaseError): tool.check_container_mapping(t)
        result.stdout=json.dumps({'Id':t['db_container_id'],'Name':'/'+t['db_container'],'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'25438'}]}}).encode()
        with patch.object(tool.subprocess,'run',return_value=result) as run:
            tool.check_container_mapping(t)
        self.assertNotIn('.Config.Env',str(run.call_args))

    def test_pg_transport_keeps_password_out_of_argv_and_streams_to_new_file(self):
        t=self.selected(); dump=self.backups/'fake.dump'
        url='postgresql://u:DO_NOT_ECHO_SECRET@127.0.0.1:25438/'+t['database_name']
        with patch.object(tool.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            tool.run_pg('pg_dump',['--format=custom'],t,url,output=dump)
        self.assertNotIn('DO_NOT_ECHO_SECRET',str(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'],'DO_NOT_ECHO_SECRET')
        self.assertIn(t['db_container_id'],run.call_args.args[0])
        with patch.object(tool.subprocess,'run') as run:
            with self.assertRaises(FileExistsError): tool.run_pg('pg_dump',[],t,url,output=dump)
        run.assert_not_called()

    def test_mock_backup_manifest_reuses_existing_builder_and_preserves_files(self):
        backup_source=SCRIPT.parents[1]/'backend/app/application/backup.py'
        fixture=self.source/'backend/app/application/backup.py'; fixture.parent.mkdir(parents=True)
        shutil.copyfile(backup_source,fixture)
        original=self.storage/'objects/sample'; original.write_bytes(b'fake disposable object')
        t=self.selected(); destination=self.backups/'new complete backup'
        def fake_dump(program,flags,target,url,**kwargs):
            self.assertEqual(program,'pg_dump'); Path(kwargs['output']).write_bytes(b'fake custom dump')
        with patch.object(tool,'runtime_dsn',return_value='not-a-real-DSN'),patch.object(tool,'check_database',return_value=0),patch.object(tool,'check_container_mapping'),patch.object(tool,'check_container_database_identity'),patch.object(tool,'run_pg',side_effect=fake_dump):
            result=tool.execute_backup(t,destination,True)
        self.assertEqual(result['status'],'BACKUP_FILES_VERIFIED_RESTORE_NOT_RUN')
        self.assertEqual((destination/'storage/objects/sample').read_bytes(),original.read_bytes())
        manifest=json.loads((destination/'manifest.json').read_text())
        self.assertEqual(manifest['native_target']['target_id'],t['target_id'])
        self.assertEqual(manifest['database']['sha256'],hashlib.sha256(b'fake custom dump').hexdigest())
        with self.assertRaises(tool.ReleaseError): tool.execute_backup(t,destination,True)

    def test_backup_failure_retained_no_cleanup_or_completion_manifest(self):
        backup_source=SCRIPT.parents[1]/'backend/app/application/backup.py'
        fixture=self.source/'backend/app/application/backup.py'; fixture.parent.mkdir(parents=True)
        shutil.copyfile(backup_source,fixture)
        t=self.selected(); destination=self.backups/'incomplete'
        with patch.object(tool,'runtime_dsn',return_value='not-a-real-DSN'),patch.object(tool,'check_database',return_value=0),patch.object(tool,'check_container_mapping'),patch.object(tool,'check_container_database_identity'),patch.object(tool,'run_pg',side_effect=tool.ReleaseError('POSTGRES_TOOL_FAILED')):
            with self.assertRaises(tool.ReleaseError): tool.execute_backup(t,destination,True)
        self.assertTrue((destination/'failure.json').is_file())
        self.assertFalse((destination/'manifest.json').exists())

    def test_unknown_config_field_is_safe_and_duplicate_json_rejected(self):
        p=self.root/'duplicate.json'; p.write_text('{"password":"DO_NOT_ECHO_SECRET","password":"another"}')
        with self.assertRaises(tool.ReleaseError) as ctx: tool.read_json(p)
        self.assertEqual(str(ctx.exception),'JSON_DUPLICATE_FIELD')


    def test_environment_allowlist_does_not_enumerate_secret_values(self):
        class OnlyGet(dict):
            def items(self): raise AssertionError('must not enumerate environment')
        env=OnlyGet(SYSTEMROOT='C:/Windows',SECRET='DO_NOT_READ')
        self.assertEqual(tool.minimal_environment(env),{'SYSTEMROOT':'C:/Windows'})
        self.assertNotIn('SECRET',tool.child_environment(self.selected(),env))



if __name__ == '__main__':
    unittest.main(verbosity=2)
