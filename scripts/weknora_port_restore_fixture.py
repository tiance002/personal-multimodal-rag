"""Restore only one explicit synthetic owner into a NEW database on approved isolation."""
import argparse,hashlib,json,shutil,subprocess,sys,asyncio,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
SOURCE='postgresql://port_fixture:synthetic-isolated-fixture@127.0.0.1:56423/weknora_python_port_20261013'
EXPECTED='7696088403504697382'
CONTAINER='weknora-python-resume-pg-20261013'
CONTAINER_ID='a5464dff5efb5e6c28811d06b9676dcd3c6910f1d5d63aaf872a619be635693d'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def main(args):
    if args.resume_empty_target and args.verify_existing:raise ValueError("RESTORE_MODE_CONFLICT")
    resume=args.resume_empty_target or args.verify_existing
    receipt_path=Path(args.scope_receipt) if args.scope_receipt else ROOT/'docs/weknora-port/evidence/P2_OCR_SPARSE_ENTRY_20261014.json'
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    original=Path(receipt['storage_root']).resolve()
    if not original.is_relative_to(Path('E:/WK/run').resolve()):raise ValueError('SYNTHETIC_SOURCE_ROOT_REQUIRED')
    destination=Path(args.output).resolve()
    if not destination.is_relative_to(Path('E:/WK/restore').resolve()) or (destination.exists() and not resume):raise ValueError('NEW_RESTORE_DIRECTORY_REQUIRED')
    if not args.database.startswith('wk_port_restore_') or not args.database.replace('_','').isalnum():raise ValueError('NEW_RESTORE_DATABASE_NAME_REQUIRED')
    identity=subprocess.check_output(['docker','inspect','--format','{{.Id}}',CONTAINER],text=True).strip()
    if identity!=CONTAINER_ID:raise ValueError('ISOLATION_CONTAINER_ID_CHANGED')
    with psycopg.connect(SOURCE,autocommit=True,row_factory=dict_row) as connection:
        if str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()['system_identifier'])!=EXPECTED:raise ValueError('PG_IDENTITY_MISMATCH')
        exists=bool(connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(args.database,)).fetchone())
        if exists and not resume:raise ValueError('RESTORE_DATABASE_ALREADY_EXISTS_HISTORY_RETAINED')
        if resume:
            marker=json.loads((destination/'owned-target.json').read_text(encoding='utf-8'))
            allowed_phases={'OWNED_DATA_RESTORED_NATIVE_READBACK_PENDING','RESTORE_VERIFIED'} if args.verify_existing else {'OWNED_SCHEMA_ONLY_EMPTY_AFTER_ROLLED_BACK_FAILURE'}
            assert marker.get('phase') in allowed_phases
            assert {k:v for k,v in marker.items() if k!='phase'}=={'database':args.database,'container_id':CONTAINER_ID,'system_identifier':EXPECTED}
            assert exists
        owner=receipt.get('tenant')
        if owner is None:
            owner=connection.execute('SELECT k.tenant_id FROM knowledges k WHERE k.id=%s',(receipt['document'],)).fetchone()['tenant_id']
        if type(owner) is not int or owner<=0:raise ValueError('EXPLICIT_SYNTHETIC_TENANT_REQUIRED')
        who=connection.execute('SELECT tenant_id,subject_id FROM memory_subjects WHERE tenant_id=%s',(owner,)).fetchone()
        if who is None:
            who=connection.execute('SELECT tenant_id,subject_id FROM port_model_configuration WHERE tenant_id=%s LIMIT 1',(owner,)).fetchone()
        if not who or not who['subject_id'].startswith('fixture-owner:'):raise ValueError('SYNTHETIC_OWNER_REQUIRED')
        if receipt.get('subject_id') and receipt['subject_id']!=who['subject_id']:raise ValueError('RESTORE_OWNER_IDENTITY_CONFLICT')
        tenant=connection.execute('SELECT * FROM tenants WHERE id=%s',(owner,)).fetchone()
        if tenant['business']!='isolated-test':raise ValueError('SYNTHETIC_TENANT_REQUIRED')
        snapshots={}
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            tables=connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
            for table in tables:
                name=table['table_name']
                columns={r['column_name'] for r in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(name,))}
                if name=='tenants':where=sql.SQL('id=%s')
                elif 'tenant_id' in columns:where=sql.SQL('tenant_id=%s')
                elif name=='embeddings':where=sql.SQL('knowledge_id IN (SELECT id FROM knowledges WHERE tenant_id=%s)')
                elif name=='send_budget_months':where=sql.SQL('account_id IN (SELECT DISTINCT account_id FROM send_calls WHERE tenant_id=%s)')
                else:continue
                query=sql.SQL("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM {} t WHERE ").format(sql.Identifier(name))+where
                rows=connection.execute(query,(owner,)).fetchone()['rows']
                if rows:snapshots[name]=rows
        if not resume:
            destination.mkdir(parents=True)
            if original.is_dir():shutil.copytree(original,destination/'storage')
            else:
                if snapshots.get('knowledges'):raise ValueError('SOURCE_STORAGE_NOT_FOUND')
                (destination/'storage').mkdir()
        files={str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
        assert all(hashlib.sha256((destination/'storage'/name).read_bytes()).hexdigest()==value for name,value in files.items())
        if resume:
            saved=json.loads((destination/'owner-snapshot.json').read_text(encoding='utf-8'))
            assert set(snapshots)-set(saved)<= {'embeddings','send_budget_months'}
            assert saved=={name:rows for name,rows in snapshots.items() if name in saved}
            snapshots=saved
        else:(destination/'owner-snapshot.json').write_text(canonical(snapshots),encoding='utf-8')
        schema=subprocess.check_output(['docker','exec',CONTAINER,'pg_dump','-U','port_fixture','-d','weknora_python_port_20261013','--schema-only','--no-owner','--no-acl'])
        if resume:
            # PG17.9 emits a fresh psql restrict nonce per dump, not a schema change.
            def schema_without_nonce(raw):
                rows=raw.splitlines()
                controls=[row for row in rows if row.startswith((b'\\restrict ',b'\\unrestrict '))]
                assert len(controls)==2 and controls[0].split()[1]==controls[1].split()[1]
                return b'\n'.join(row for row in rows if row not in controls)
            saved_schema=(destination/'schema.sql').read_bytes()
            assert schema_without_nonce(saved_schema)==schema_without_nonce(schema)
            schema=saved_schema
        else:
            (destination/'schema.sql').write_bytes(schema)
            connection.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(args.database)))
    if not resume:
        loaded=subprocess.run(['docker','exec','-i',CONTAINER,'psql','-U','port_fixture','-d',args.database,'-v','ON_ERROR_STOP=1'],input=schema,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        (destination/'schema-load.stdout.txt').write_bytes(loaded.stdout)
        if loaded.returncode:raise RuntimeError('NEW_TARGET_SCHEMA_RESTORE_FAILED')
    if not resume:
        (destination/'owned-target.json').write_text(json.dumps({'database':args.database,'container_id':CONTAINER_ID,'system_identifier':EXPECTED,'phase':'OWNED_SCHEMA_ONLY_EMPTY_AFTER_ROLLED_BACK_FAILURE'}),encoding='utf-8')
    target=SOURCE.rsplit('/',1)[0]+'/'+args.database
    changed_paths=[]
    with psycopg.connect(target,autocommit=True,row_factory=dict_row) as restored:
        if str(restored.execute('SELECT system_identifier FROM pg_control_system()').fetchone()['system_identifier'])!=EXPECTED:raise ValueError('RESTORE_TARGET_IDENTITY_CHANGED')
        if resume and not args.verify_existing:
            for row in restored.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"):
                if row['table_name']=='spatial_ref_sys':
                    # PostGIS initializes its immutable reference catalogue on CREATE EXTENSION.
                    checksum="SELECT count(*) AS n,md5(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY srid)) AS h FROM spatial_ref_sys t"
                    with psycopg.connect(SOURCE,autocommit=True,row_factory=dict_row) as source_check:
                        assert source_check.execute(checksum).fetchone()==restored.execute(checksum).fetchone()
                    continue
                assert restored.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(row['table_name']))).fetchone()['n']==0,'RESTORE_TARGET_NOT_EMPTY'
        if not args.verify_existing:
            with restored.transaction():
                restored.execute('SET LOCAL session_replication_role=replica')
                for name,rows in snapshots.items():
                    for row in rows:
                        row=dict(row)
                        if name=='knowledges' and row.get('file_path'):
                            sourcepath=Path(row['file_path']).resolve()
                            if not sourcepath.is_relative_to(original):raise ValueError('BACKUP_SOURCE_PATH_ESCAPE')
                            row['file_path']=str(destination/'storage'/sourcepath.relative_to(original));changed_paths.append(row['id'])
                        restored.execute(sql.SQL('INSERT INTO {} SELECT * FROM jsonb_populate_record(NULL::{},%s::jsonb)').format(sql.Identifier(name),sql.Identifier(name)),(json.dumps(row),))
                # Only new target sequences; original database sequence state untouched.
                for name in snapshots:
                    if not restored.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name='id'",(name,)).fetchone():continue
                    serial=restored.execute("SELECT pg_get_serial_sequence(%s,'id') AS seq",(name,)).fetchone()['seq']
                    if serial:
                        maximum=restored.execute(sql.SQL('SELECT max(id) AS n FROM {}').format(sql.Identifier(name))).fetchone()['n']
                        restored.execute('SELECT setval(%s,%s,true)',(serial,max(1,maximum or 1)))
        verified={}
        for name,rows in snapshots.items():
            query=sql.SQL("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM {} t").format(sql.Identifier(name))
            actual=restored.execute(query).fetchone()['rows']
            for row in actual:
                if name=='knowledges' and row.get('file_path'):
                    changed_paths.append(row['id']) if row['id'] not in changed_paths else None
                    row['file_path']=str(original/Path(row['file_path']).relative_to(destination/'storage'))
            # Paths normalize to the original backup namespace for comparison only.
            expected=sorted(rows,key=canonical);actual=sorted(actual,key=canonical)
            assert actual==expected,name
            verified[name]={'rows':len(rows),'sha256':digest(expected)}
        # Existing rehearsal snapshot predates index-owner join; preserve it and add a separately hashed supplement.
        if 'embeddings' not in snapshots:
            with psycopg.connect(SOURCE,autocommit=True,row_factory=dict_row) as source_check:
                with source_check.transaction():
                    source_check.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    indices=source_check.execute("SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY to_jsonb(e)::text),'[]'::jsonb) AS rows FROM embeddings e WHERE knowledge_id IN (SELECT id FROM knowledges WHERE tenant_id=%s)",(owner,)).fetchone()['rows']
            supplement=destination/'index-supplement.json'
            if supplement.exists():assert json.loads(supplement.read_text(encoding='utf-8'))==indices
            else:supplement.write_text(canonical(indices),encoding='utf-8')
            count=restored.execute('SELECT count(*) AS n FROM embeddings').fetchone()['n']
            if count==0:
                with restored.transaction():
                    for row in indices:restored.execute('INSERT INTO embeddings SELECT * FROM jsonb_populate_record(NULL::embeddings,%s::jsonb)',(json.dumps(row),))
                    sequence=restored.execute("SELECT pg_get_serial_sequence('embeddings','id') AS seq").fetchone()['seq']
                    if sequence:restored.execute('SELECT setval(%s,%s,true)',(sequence,max([r['id'] for r in indices],default=1)))
            actual=restored.execute("SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY to_jsonb(e)::text),'[]'::jsonb) AS rows FROM embeddings e").fetchone()['rows']
            assert sorted(actual,key=canonical)==sorted(indices,key=canonical),'RESTORED_INDEX_BYTES_MISMATCH'
            verified['embeddings']={'rows':len(indices),'sha256':digest(sorted(indices,key=canonical)),'supplement_sha256':hashlib.sha256(supplement.read_bytes()).hexdigest()}
        # Preserve referenced monthly authority/blocked state; never reset an account budget.
        if 'send_budget_months' not in snapshots:
            with psycopg.connect(SOURCE,autocommit=True,row_factory=dict_row) as source_check:
                with source_check.transaction():
                    source_check.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    months=source_check.execute("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM send_budget_months t WHERE account_id IN (SELECT DISTINCT account_id FROM send_calls WHERE tenant_id=%s)",(owner,)).fetchone()['rows']
            supplement=destination/'account-budget-supplement.json'
            if supplement.exists():assert json.loads(supplement.read_text(encoding='utf-8'))==months
            else:supplement.write_text(canonical(months),encoding='utf-8')
            actual=restored.execute("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM send_budget_months t").fetchone()['rows']
            if not actual:
                with restored.transaction():
                    for row in months:restored.execute('INSERT INTO send_budget_months SELECT * FROM jsonb_populate_record(NULL::send_budget_months,%s::jsonb)',(json.dumps(row),))
                actual=restored.execute("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM send_budget_months t").fetchone()['rows']
            assert sorted(actual,key=canonical)==sorted(months,key=canonical),'RESTORED_BUDGET_AUTHORITY_MISMATCH'
            verified['send_budget_months']={'rows':len(months),'sha256':digest(sorted(months,key=canonical)),'supplement_sha256':hashlib.sha256(supplement.read_bytes()).hexdigest()}
        with psycopg.connect(SOURCE,autocommit=True,row_factory=dict_row) as source_check:
            shared_accounts=source_check.execute('SELECT count(*) AS n FROM (SELECT account_id FROM send_calls WHERE account_id IN (SELECT account_id FROM send_calls WHERE tenant_id=%s) GROUP BY account_id HAVING count(DISTINCT tenant_id)>1) t',(owner,)).fetchone()['n']
        active_indices=restored.execute('SELECT count(*) AS n FROM embeddings WHERE is_enabled=true').fetchone()['n']
        unknown=restored.execute("SELECT count(*) AS n,COALESCE(sum(reserved_microunits),0) AS occupied FROM send_calls WHERE state='unknown'").fetchone()
    (destination/'owned-target.json').write_text(json.dumps({'database':args.database,'container_id':CONTAINER_ID,'system_identifier':EXPECTED,'phase':'OWNED_DATA_RESTORED_NATIVE_READBACK_PENDING'}),encoding='utf-8')
    async def native_readback():
        from backend.weknora_port.storage.connections import IsolatedConnectionFactory
        from backend.weknora_port.storage.postgres import IsolationPolicy
        from backend.weknora_port.storage.history import LocalPrincipal
        from backend.weknora_port.documents import DocumentLifecycleService
        from backend.weknora_port.storage.memory import PostgresMemoryRepository
        from backend.weknora_port.storage.history import PostgresHistoryRepository
        async def acquire():
            connection=await psycopg.AsyncConnection.connect(target,autocommit=True,row_factory=dict_row)
            if connection.info.dbname!=args.database:raise ValueError('RESTORE_DATABASE_MISMATCH')
            return connection
        async def release(conn):await conn.close()
        leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(EXPECTED,frozenset({'7691227493754040358','7689510505384681511','7696038373595934758'}),database=args.database))
        principal=LocalPrincipal(who['tenant_id'],who['subject_id'])
        # No provider adapter/permissions/cost authority supplied; source read is local.
        documents=DocumentLifecycleService(leases,destination/'storage',principal=principal,resolve_kb_settings=lambda *args:None)
        readbacks=[];chunk_count=0
        for knowledge in snapshots.get('knowledges',[]):
            data=await documents.read_document(principal,knowledge['knowledge_base_id'],knowledge['id'])
            assert hashlib.sha256(data.content).hexdigest()==knowledge['file_hash']
            async with leases.lease() as guard:
                chunks=await (await guard.connection.execute('SELECT id FROM chunks WHERE tenant_id=%s AND knowledge_id=%s AND is_enabled=true',(owner,knowledge['id']))).fetchall()
            for row in chunks:assert (await documents.read_source_chunk(principal,row['id']))['text']
            chunk_count+=len(chunks);readbacks.append({'document':knowledge['id'],'sha256':hashlib.sha256(data.content).hexdigest()})
        from backend.weknora_port.conversation.types import MemoryScope
        histories=[];generation_replays=0
        async with leases.lease() as guard:
            history=PostgresHistoryRepository(guard,principal)
            for row in snapshots.get('messages',[]):
                if row['role']=='assistant' and row.get('deleted_at') is None:
                    run=await history.validate_assistant_message(row['session_id'],row['id'])
                    assert run.request_id==row['request_id'] and run.message_id==row['id']
                    foreign=PostgresHistoryRepository(guard,LocalPrincipal(owner,principal.subject_id+':foreign'))
                    try:await foreign.validate_assistant_message(row['session_id'],row['id'])
                    except LookupError:pass
                    else:raise AssertionError('RESTORED_HISTORY_OWNER_BOUNDARY_BROKEN')
                    histories.append({'assistant':row['id'],'status':row.get('turn_status'),'is_completed':row['is_completed'],'content_sha256':hashlib.sha256(row['content'].encode()).hexdigest()})
            from backend.weknora_port.storage.generation import PostgresGenerationLifecycle
            from backend.weknora_port.generation.types import GenerationReplay
            from backend.weknora_port.models.types import CallContext,TrustedScope
            for run in snapshots.get('generation_runs',[]):
                context=CallContext(request_id=run['run_id'],scope=TrustedScope(principal.subject_id,tuple(run['knowledge_base_ids']),tuple(run['document_ids'])),session_id=run['session_id'])
                lifecycle=PostgresGenerationLifecycle(guard,tenant_id=owner,session_id=run['session_id'],message_id=run['message_id'])
                try:await lifecycle.claim_run(run['run_id'],run['payload_sha256'],context)
                except GenerationReplay:generation_replays+=1
                else:raise AssertionError('RESTORED_GENERATION_REPLAY_NOT_DENIED')
                current=(await (await guard.connection.execute('SELECT to_jsonb(t) AS row FROM generation_runs t WHERE tenant_id=%s AND run_id=%s',(owner,run['run_id']))).fetchone())['row']
                assert current==run,'RESTORED_RUN_CHANGED_ON_REPLAY'
            memory=PostgresMemoryRepository(guard.connection,IsolationPolicy(EXPECTED,frozenset({'7691227493754040358','7689510505384681511','7696038373595934758'}),database=args.database),identity_guard=guard)
            scope=MemoryScope(owner,principal.subject_id)
            items=await memory.list_items(scope,'',20000,0)
            tombstones=await memory.list_tombstones(scope,20000)
            assert len(items)==len(snapshots.get('memory_items',[]))
            assert len(tombstones)==len(snapshots.get('memory_tombstones',[]))
            foreign_scope=MemoryScope(owner,principal.subject_id+':foreign')
            assert not await memory.list_items(foreign_scope,'',50,0)
        return {'originals':readbacks,'active_chunks_readback':chunk_count,'history_assistants':histories,'generation_replays_denied_without_write':generation_replays,'memory_items':len(items),'memory_tombstones':len(tombstones),'foreign_history_and_memory_denied':True}
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:native=runner.run(native_readback())
    result={'kind':'CONSISTENT_SYNTHETIC_OWNER_RESTORE_NEW_DATABASE_NATIVE_READBACK','source_system_identifier':EXPECTED,'source_tenant':owner,'target_database':args.database,
        'container_reused_without_rename':CONTAINER,'new_container':False,'new_volume':False,'artifact_root':str(destination),'snapshot_tables':verified,
        'copied_immutable_files':len(files),'storage_hashes':files,'schema_sha256':hashlib.sha256(schema).hexdigest(),'snapshot_sha256':hashlib.sha256((destination/'owner-snapshot.json').read_bytes()).hexdigest(),
        'path_mapping_document_ids':changed_paths,'unknown_preserved':unknown,'referenced_account_months_preserved':verified.get('send_budget_months',{}),'shared_accounts_in_source':shared_accounts,'shared_account_full_ledger_migration':'NOT_CLAIMED' if shared_accounts else 'NOT_APPLICABLE','restored_active_indices':active_indices,'native_entry':native,'real_provider_calls':0,'old_business_database_accessed':False,'cutover':'NOT_RUN'}
    report=Path(args.report).resolve() if args.report else ROOT/'docs/weknora-port/evidence/P8_SYNTHETIC_RESTORE_20261014.json'
    if not report.is_relative_to((ROOT/'docs/weknora-port/evidence').resolve()):raise ValueError('RESTORE_REPORT_PATH_REQUIRED')
    report.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    (destination/'owned-target.json').write_text(json.dumps({'database':args.database,'container_id':CONTAINER_ID,'system_identifier':EXPECTED,'phase':'RESTORE_VERIFIED'}),encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--output',required=True);parser.add_argument('--resume-empty-target',action='store_true');parser.add_argument('--verify-existing',action='store_true');parser.add_argument('--scope-receipt');parser.add_argument('--report');main(parser.parse_args())
