"""Scoped S8 logical bundle: trusted read-only export, verified target-only import.

No whole-database dump, local principal copy, plaintext keys, queue recreation,
Provider call or source database write. Import is explicit and never automatic.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, re, shutil, subprocess, sys, tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
READ=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
ACTIVE=('tenants','models','port_model_configuration','knowledge_bases','knowledges','chunks','chunk_revisions',
        'embeddings','sessions','messages','port_turns','generation_runs','generation_attempts',
        'port_provider_calls','port_graph_evidence','port_graph_nodes','port_graph_edges')
ARCHIVE=('port_image_tasks','port_call_slots','send_budget_months','send_calls','port_token_budget_months','port_token_calls')
PHYSICAL_KEYS={'path','file_path','image_url','url','local_path','source_path','storage_path'}


def sha(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()
def native(path):
    path=Path(path)
    return Path('\\\\?\\'+str(path.resolve())) if os.name=='nt' and not str(path).startswith('\\\\?\\') else path
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def jsonobj(value):
    if isinstance(value,dict):return value
    if isinstance(value,str):
        try:return json.loads(value)
        except ValueError:pass
    return {}
def scopes(row,key):
    value=row.get(key) or []
    return json.loads(value) if isinstance(value,str) else value


def scope_tail(path,document):
    """Only an actual tenant/KB/document/version path can establish a root."""
    normalized=str(path).replace('\\','/')
    anchor='/'.join(('332',document['knowledge_base_id'],document['id']))+'/'
    offset=normalized.find(anchor)
    if offset<0:return None
    tail=PurePosixPath(normalized[offset:])
    if '..' in tail.parts or not tail.parts or tail.parts[0]!='332':raise ValueError('ASSET_SCOPE_ESCAPE')
    return tail


def portable_relative(relative):
    parts=[]
    for part in PurePosixPath(relative).parts:
        if part in ('..','/','') or '\\' in part:raise ValueError('ASSET_SCOPE_ESCAPE')
        suffix=PurePosixPath(part).suffix
        if not re.fullmatch(r'\.[A-Za-z0-9]{1,12}',suffix):suffix=''
        parts.append(part if len(part.encode('utf-8'))<=240 else 'migrated-'+sha(part)[:24]+suffix)
    return PurePosixPath(*parts)


def assert_scope(tables,kbs,subject):
    allowed=set(kbs)
    for table,rows in tables.items():
        if table not in ACTIVE+ARCHIVE:raise ValueError('TABLE_NOT_WHITELISTED')
        for row in rows:
            if table=='tenants' and row.get('id')!=332:raise ValueError('FOREIGN_TENANT_IN_BUNDLE')
            if 'tenant_id' in row and row['tenant_id']!=332:raise ValueError('FOREIGN_TENANT_IN_BUNDLE')
            for key in ('subject_id','owner_id'):
                if key in row and row[key]!=subject:raise ValueError('FOREIGN_SUBJECT_IN_BUNDLE')
            if table in ('knowledge_bases','knowledges','chunks','chunk_revisions','embeddings','port_graph_evidence','port_image_tasks'):
                kb=row['id'] if table=='knowledge_bases' else row.get('knowledge_base_id')
                if kb not in allowed:raise ValueError('FOREIGN_KB_IN_BUNDLE')
            if table in ('sessions','messages','port_turns','generation_runs'):
                for key in ('knowledge_base_scope','knowledge_base_ids'):
                    if not set(scopes(row,key))<=allowed:raise ValueError('FOREIGN_KB_IN_HISTORY')


def validate_closure(tables,foreign_keys):
    for fk in foreign_keys:
        if fk['table_name'] not in tables:continue
        parents={tuple(row.get(c) for c in fk['referenced_columns']) for row in tables.get(fk['referenced_table'],[])}
        for row in tables[fk['table_name']]:
            key=tuple(row.get(c) for c in fk['columns'])
            if any(value is None for value in key):continue
            if key not in parents:raise ValueError('FK_CLOSURE_MISSING:'+fk['table_name']+'->'+fk['referenced_table'])


def map_physical(value,mapping,key=''):
    """Preserve prose, quotations, hashes, logical paths and ciphertext exactly."""
    if isinstance(value,list):return [map_physical(item,mapping,key) for item in value]
    if isinstance(value,dict):return {name:map_physical(item,mapping,name) for name,item in value.items()}
    return mapping.get(value,value) if isinstance(value,str) and key in PHYSICAL_KEYS else value


def imported_row(table,row,mapping):
    result=dict(row)
    if table=='knowledges':
        result['file_path']=mapping.get(row.get('file_path'),row.get('file_path'))
        result['metadata']=map_physical(row.get('metadata'),mapping)
    if table=='chunks':
        result['metadata']=map_physical(row.get('metadata'),mapping)
        if row.get('image_info'):
            parsed=jsonobj(row['image_info'])
            if parsed:result['image_info']=json.dumps(map_physical(parsed,mapping),ensure_ascii=False,separators=(',',':'))
    return result


def collect_assets(tables,output,target_root):
    manifest=[];missing=[];mapping={};seen={};targets={};relative_mapping={}
    for document in tables['knowledges']:
        original=Path(document['file_path']);tail=scope_tail(original,document)
        if tail is None:
            missing.append({'document_id':document['id'],'kind':'UNMAPPED_ORIGINAL_SCOPE','path':str(original)});continue
        prefix=Path(str(original).replace('\\','/')[:str(original).replace('\\','/').find(tail.as_posix())])
        document_root=prefix.joinpath(*tail.parts[:3])
        files=[]
        if native(document_root).is_dir():
            files=[Path(str(p)[4:] if str(p).startswith('\\\\?\\') else str(p))
                   for p in native(document_root).rglob('*') if p.is_file() and not p.is_symlink()]
        if native(original).is_file() and original not in files:files.append(original)
        for source in files:
            relative=scope_tail(source,document)
            if relative is None or not source.resolve().is_relative_to(document_root.resolve()):raise ValueError('ASSET_PATH_OUTSIDE_DOCUMENT')
            raw=native(source).read_bytes();digest=sha(raw);rel=relative.as_posix();portable=portable_relative(relative);destination=portable.as_posix();dest=output/'assets'/Path(*portable.parts)
            if rel in seen:
                if seen[rel]!=digest:raise ValueError('ASSET_TARGET_COLLISION')
                continue
            if destination in targets and targets[destination]!=digest:raise ValueError('PORTABLE_ASSET_TARGET_COLLISION')
            native(dest).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(native(source),native(dest));seen[rel]=digest
            targets[destination]=digest;relative_mapping[rel]=destination
            target=(PurePosixPath(target_root)/portable).as_posix()
            mapping[str(source)]=target;mapping[str(source).replace('\\','/')]=target
            mapping[rel.replace('/','\\')]=destination;mapping[rel]=destination
            manifest.append({'relative_path':destination,'source_relative_path':rel,'sha256':digest,'bytes':len(raw),'document_id':document['id'],
                             'target_path':target,'original_file':source.resolve()==original.resolve()})
        original_entry=next((row for row in manifest if row['document_id']==document['id'] and row['original_file']),None)
        if original_entry is None:missing.append({'document_id':document['id'],'kind':'MISSING_ORIGINAL','path':str(original)})
        elif original_entry['sha256']!=document['file_hash']:raise ValueError('ORIGINAL_SHA_MISMATCH')
        for asset in jsonobj(document.get('metadata')).get('assets',[]):
            if not isinstance(asset,dict) or not isinstance(asset.get('path'),str):continue
            stored=asset['path'];relative=stored.replace('\\','/')
            if re.match(r'^[A-Za-z]:/',relative):
                actual=Path(stored);asset_tail=scope_tail(actual,document)
            else:actual=prefix/Path(relative);asset_tail=scope_tail(relative,document)
            if not native(actual).is_file():missing.append({'document_id':document['id'],'kind':'MISSING_REFERENCED_ASSET','path':stored});continue
            if asset_tail and asset_tail.as_posix() in seen:
                if asset.get('sha256') and asset['sha256']!=seen[asset_tail.as_posix()]:raise ValueError('ASSET_MANIFEST_SHA_MISMATCH')
                portable=relative_mapping[asset_tail.as_posix()]
                mapping[stored]=portable if not re.match(r'^[A-Za-z]:',stored) else (PurePosixPath(target_root)/portable).as_posix()
    return manifest,missing,mapping


async def export_bundle(args):
    from scripts.local_runtime import load_config,environment,verify_resources
    from scripts.s2_generation_quality import identity_for
    from backend.weknora_port.development import DevelopmentSettings,assemble
    output=args.output.resolve()
    if output.exists():raise ValueError('BUNDLE_OUTPUT_ALREADY_EXISTS')
    qa=READ(args.qa);omni=READ(args.omni);kbs=sorted({row['knowledge_base_id'] for row in qa+omni})
    assert len(qa)==40 and len(omni)==24 and len(kbs)==7
    cfg=load_config(args.config);physical=await verify_resources(cfg);env=environment(cfg,physical['redis_run_id'])
    env.update(WEKNORA_PORT_CLOUD_ALLOWED='false',WEKNORA_PORT_GRAPH_ENABLED='false')
    for key in ('WEKNORA_PORT_EXECUTION_FILE','WEKNORA_PORT_EVIDENCE_DIR'):env.pop(key,None)
    env,principal=identity_for(332,env,READ(args.principal_state));settings=DevelopmentSettings.from_environment(env)
    tables={name:[] for name in ACTIVE+ARCHIVE};schemas={}
    async with assemble(settings,principal=principal) as app:
      async with app.leases.lease() as guard:
       async with guard.connection.transaction():
        await guard.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        async def fetch(table,where,args=()):
            if table not in tables:raise ValueError('TABLE_NOT_WHITELISTED')
            return [row['row'] for row in await(await guard.connection.execute('SELECT to_jsonb(t) AS row FROM '+table+' t WHERE '+where,args)).fetchall()]
        async def query(sql,values=()):return await(await guard.connection.execute(sql,values)).fetchall()
        before=(await query("SELECT count(*) AS count,count(*) FILTER(WHERE outcome='UNKNOWN') AS unknown FROM port_provider_calls"))[0]
        cols=await query("SELECT table_name,column_name,data_type,udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name,ordinal_position",(list(tables),))
        for row in cols:schemas.setdefault(row['table_name'],[]).append(row)
        tables['tenants']=await fetch('tenants','id=332 AND deleted_at IS NULL')
        tables['knowledge_bases']=await fetch('knowledge_bases','tenant_id=332 AND id=ANY(%s)',(kbs,))
        tables['knowledges']=await fetch('knowledges','tenant_id=332 AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL',(kbs,))
        docs=[row['id'] for row in tables['knowledges']]
        tables['chunks']=await fetch('chunks','tenant_id=332 AND knowledge_base_id=ANY(%s) AND knowledge_id=ANY(%s)',(kbs,docs))
        chunks=[row['id'] for row in tables['chunks']]
        tables['chunk_revisions']=await fetch('chunk_revisions','tenant_id=332 AND knowledge_base_id=ANY(%s) AND chunk_id=ANY(%s)',(kbs,chunks))
        tables['embeddings']=await fetch('embeddings','knowledge_base_id=ANY(%s) AND knowledge_id=ANY(%s) AND chunk_id=ANY(%s)',(kbs,docs,chunks))
        if (len(tables['knowledge_bases']),len(docs),len(tables['embeddings']))!=(7,84,739):raise ValueError('SOURCE_SCOPE_COUNT_CHANGED')
        for table in ('models','port_model_configuration','port_provider_calls','port_call_slots','port_token_calls','send_calls'):
            tables[table]=await fetch(table,'tenant_id=332 AND subject_id=%s',(principal.subject_id,))
        all_sessions=await fetch('sessions','tenant_id=332 AND subject_id=%s',(principal.subject_id,))
        tables['sessions']=[row for row in all_sessions if scopes(row,'knowledge_base_scope') and set(scopes(row,'knowledge_base_scope'))<=set(kbs)
                            and set(scopes(row,'document_scope'))<=set(docs)]
        sessions=[row['id'] for row in tables['sessions']]
        for table in ('messages','port_turns','generation_runs'):
            tables[table]=await fetch(table,'tenant_id=332 AND subject_id=%s AND session_id=ANY(%s)',(principal.subject_id,sessions))
        runs=[row['run_id'] for row in tables['generation_runs']]
        tables['generation_attempts']=await fetch('generation_attempts','tenant_id=332 AND subject_id=%s AND run_id=ANY(%s)',(principal.subject_id,runs))
        tables['port_image_tasks']=await fetch('port_image_tasks','tenant_id=332 AND subject_id=%s AND knowledge_base_id=ANY(%s) AND knowledge_id=ANY(%s)',(principal.subject_id,kbs,docs))
        tables['port_graph_evidence']=await fetch('port_graph_evidence','tenant_id=332 AND owner_id=%s AND knowledge_base_id=ANY(%s) AND document_id=ANY(%s)',(principal.subject_id,kbs,docs))
        graph=[row['id'] for row in tables['port_graph_evidence']]
        for table in ('port_graph_nodes','port_graph_edges'):tables[table]=await fetch(table,'evidence_id=ANY(%s)',(graph,))
        for table,calls,fields in (('port_token_budget_months',tables['port_token_calls'],('account_id','budget_provider','month')),('send_budget_months',tables['send_calls'],('account_id','budget_month'))):
            keys={tuple(row[field] for field in fields) for row in calls}
            if keys:
                actual=('account_id','provider','month') if table.startswith('port_') else ('account_id','month')
                clauses=' OR '.join('('+' AND '.join(field+'=%s' for field in actual)+')' for _ in keys)
                tables[table]=await fetch(table,clauses,tuple(value for key in sorted(keys) for value in key))
        foreign_keys=await query("SELECT c.conrelid::regclass::text AS table_name,c.confrelid::regclass::text AS referenced_table,ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.ord) AS columns,ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.attnum ORDER BY k.ord) AS referenced_columns FROM pg_constraint c WHERE c.contype='f' AND c.connamespace='public'::regnamespace")
        after=(await query("SELECT count(*) AS count,count(*) FILTER(WHERE outcome='UNKNOWN') AS unknown FROM port_provider_calls"))[0]
        assert before==after
    assert_scope(tables,kbs,principal.subject_id);validate_closure(tables,foreign_keys)
    docmap={row['id']:row for row in tables['knowledges']};chunkmap={row['id']:row for row in tables['chunks']}
    for row in tables['embeddings']:
        chunk=chunkmap[row['chunk_id']];doc=docmap[row['knowledge_id']]
        if chunk['knowledge_id']!=doc['id'] or chunk['knowledge_base_id']!=row['knowledge_base_id'] or doc['knowledge_base_id']!=row['knowledge_base_id']:raise ValueError('INDEX_IDENTITY_MISMATCH')
        if row['dimension']!=1024 or row['embedding'] is None:raise ValueError('VECTOR_NOT_1024_NON_NULL')
    for row in tables['chunks']:
        for field in ('parent_chunk_id','pre_chunk_id','next_chunk_id'):
            if row.get(field) and row[field] not in chunkmap:raise ValueError('CHUNK_RELATION_CLOSURE_MISSING')
    if len(graph)!=19:raise ValueError('GRAPH_SCOPE_CHANGED')
    output.mkdir(parents=True)
    assets,missing,mapping=collect_assets(tables,output,args.target_storage_root)
    file_manifest=[]
    for table,rows in tables.items():
        group='tables' if table in ACTIVE else 'preservation-only'
        path=output/group/(table+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:
            for row in rows:stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        file_manifest.append({'table':table,'path':path.relative_to(output).as_posix(),'rows':len(rows),'sha256':sha(path.read_bytes()),'bytes':path.stat().st_size,'import':table in ACTIVE})
    write(output/'physical-path-map.json',mapping)
    manifest={'schema':'s8-scoped-logical-bundle/v1','status':'BUNDLE_READY' if not missing else 'BUNDLE_PARTIAL_MISSING_ASSETS',
        'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_identity':{key:physical[key] for key in ('postgres_system_id','database','redis_run_id','redis_db')},
        'scope':{'tenant_id':332,'subject_id':principal.subject_id,'subject_sha256':sha(principal.subject_id),'knowledge_base_ids':kbs,'documents':84,'vector_rows':739},
        'target_storage_root':args.target_storage_root,'model_credentials':'EXACT_STORED_CIPHERTEXT_ONLY; source key delivered separately by Owner coordinator',
        'secret_key_sha256':sha(settings.secret_key_file.read_bytes()),'source_local_principal_copied':False,
        'permissions':'Source tenant/subject and exact KB cloud_allowed retained; no new account/RBAC or cloud grant created',
        'queue_policy':'Redis queues not exported; all old image tasks preservation-only, no pending replay',
        'legacy_policy':'Retired send/token authority rows are preservation-only; never reactivate from this bundle',
        'new_ingestion_requirement':'Raw-only BGE via formal target configuration; no new Summary/Caption/Graph/question generation unless separately authorized',
        'tables':file_manifest,'assets':assets,'missing_assets':missing,'foreign_keys':foreign_keys,'table_columns':schemas,
        'path_map_sha256':sha((output/'physical-path-map.json').read_bytes()),'provider_before':before,'provider_after':after,'new_provider_calls':0,'source_database_writes':0,
        'indexed_chunk_types':dict(Counter(chunkmap[row['chunk_id']]['chunk_type'] for row in tables['embeddings'])),
        'existing_unknowns':{name:sum(row.get('outcome')=='UNKNOWN' or row.get('state')=='unknown' for row in rows) for name,rows in tables.items() if name in ('port_provider_calls','port_token_calls','send_calls')}}
    write(output/'manifest.json',manifest)
    if args.archive:
        with tarfile.open(args.archive,'x:gz') as archive:archive.add(native(output),arcname=output.name)
    print(json.dumps({'status':manifest['status'],'documents':84,'vectors':739,'tables':{row['table']:row['rows'] for row in file_manifest},'assets':len(assets),'missing_assets':len(missing),'manifest_sha256':sha((output/'manifest.json').read_bytes()),'archive':str(args.archive) if args.archive else None},ensure_ascii=True))
    return manifest


def verify_bundle(path):
    manifest=READ(path/'manifest.json')
    if manifest.get('schema')!='s8-scoped-logical-bundle/v1':raise ValueError('BUNDLE_SCHEMA_MISMATCH')
    rows={}
    for item in manifest['tables']:
        file=path/item['path']
        if sha(file.read_bytes())!=item['sha256']:raise ValueError('BUNDLE_TABLE_SHA_MISMATCH')
        rows[item['table']]=[json.loads(line) for line in file.read_text(encoding='utf-8').splitlines() if line]
        if len(rows[item['table']])!=item['rows']:raise ValueError('BUNDLE_TABLE_COUNT_MISMATCH')
    assert_scope(rows,manifest['scope']['knowledge_base_ids'],manifest['scope']['subject_id']);validate_closure(rows,manifest['foreign_keys'])
    for item in manifest['assets']:
        if sha(native(path/'assets'/item['relative_path']).read_bytes())!=item['sha256']:raise ValueError('BUNDLE_ASSET_SHA_MISMATCH')
    if sha((path/'physical-path-map.json').read_bytes())!=manifest['path_map_sha256']:raise ValueError('BUNDLE_PATH_MAP_SHA_MISMATCH')
    return manifest,rows


async def import_bundle(args):
    """Root-only explicit apply on a new verified target; never the source DB."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    if not args.apply or not args.target_system_id:raise ValueError('EXPLICIT_TARGET_APPLY_AND_ID_REQUIRED')
    manifest,tables=verify_bundle(args.output)
    if manifest['status']!='BUNDLE_READY':raise ValueError('BUNDLE_HAS_MISSING_ASSETS')
    dsn=os.environ.get('WEKNORA_PORT_DATABASE_URL','')
    if not dsn:raise ValueError('TARGET_DATABASE_URL_ENV_REQUIRED')
    connection=await psycopg.AsyncConnection.connect(dsn,autocommit=True,row_factory=dict_row,connect_timeout=10)
    try:
        identity=await(await connection.execute('SELECT current_database() AS database,system_identifier::text AS system_id FROM pg_control_system()')).fetchone()
        if (identity['system_id']!=args.target_system_id or identity['system_id']==manifest['source_identity']['postgres_system_id']
            or not identity['database'].startswith('weknora_')):raise ValueError('TARGET_PHYSICAL_IDENTITY_REQUIRED')
        # Source key is transported separately; never serialize or log it here.
        keyfile=os.environ.get('WEKNORA_PORT_SECRET_KEY_FILE','')
        if not keyfile or sha(Path(keyfile).read_bytes())!=manifest['secret_key_sha256']:raise ValueError('TARGET_CIPHER_KEY_MISMATCH')
        destination=args.asset_destination or Path(manifest['target_storage_root'])
        if not destination.is_absolute():raise ValueError('ABSOLUTE_TARGET_ASSET_PATH_REQUIRED')
        for item in manifest['assets']:
            source=args.output/'assets'/item['relative_path'];target=destination/item['relative_path']
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('ASSET_TARGET_ESCAPE')
            if target.exists():
                if sha(target.read_bytes())!=item['sha256']:raise ValueError('TARGET_ASSET_CONFLICT')
            else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        mapping=READ(args.output/'physical-path-map.json')
        async with connection.transaction():
            for table in (*ACTIVE,'port_local_principal'):
                count=await(await connection.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)))).fetchone()
                if count['n']:raise ValueError('TARGET_TABLE_NOT_EMPTY:'+table)
            for table in ACTIVE:
                actual=await(await connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,))).fetchall()
                if not {col['column_name'] for col in manifest['table_columns'][table]}<={col['column_name'] for col in actual}:raise ValueError('TARGET_SCHEMA_COLUMNS_MISMATCH:'+table)
                statement=sql.SQL('INSERT INTO {} SELECT * FROM jsonb_populate_record(NULL::{},%s::jsonb)').format(sql.Identifier(table),sql.Identifier(table))
                for row in tables[table]:await connection.execute(statement,(json.dumps(imported_row(table,row,mapping),ensure_ascii=False),))
            if args.bind_target_principal:
                await connection.execute("INSERT INTO port_local_principal(slot,tenant_id,subject_id) VALUES('local',332,%s)",(manifest['scope']['subject_id'],))
            # Explicit source serial IDs were retained; advance only new target sequences.
            for table in ('tenants','embeddings','chunk_revisions'):
                sequence=await(await connection.execute('SELECT pg_get_serial_sequence(%s,%s) AS sequence',(table,'id'))).fetchone()
                if sequence['sequence']:
                    maximum=await(await connection.execute(sql.SQL('SELECT max(id) AS maximum FROM {}').format(sql.Identifier(table)))).fetchone()
                    if maximum['maximum'] is not None:await connection.execute('SELECT setval(%s,%s,true)',(sequence['sequence'],maximum['maximum']))
        result={'schema':'s8-scoped-import-receipt/v1','status':'TARGET_LOGICAL_IMPORT_COMPLETE_REQUIRES_RUNTIME_VERIFICATION',
            'target_identity':identity,'table_counts':{table:len(tables[table]) for table in ACTIVE},'assets':len(manifest['assets']),
            'source_unchanged':True,'unknowns_preserved':manifest['existing_unknowns'],'legacy_archive_imported':False,
            'old_image_tasks_imported':False,'redis_queues_imported':False,'target_principal_bound':args.bind_target_principal,'new_provider_calls':0}
        write(args.output/'target-import-receipt.json',result);print(json.dumps(result,ensure_ascii=True));return result
    finally:await connection.close()


def arguments(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('export','verify','import'))
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--archive',type=Path)
    parser.add_argument('--target-storage-root',default='/var/lib/eviroute/storage')
    parser.add_argument('--config',type=Path,default=Path('E:/WK/run/s4-quality-20261019/local.json'))
    parser.add_argument('--principal-state',type=Path,default=Path('E:/WK/run/p25-20261017/runtime/state.json'))
    parser.add_argument('--qa',type=Path,default=Path('E:/WK/run/m4-full-sweep-20261018/qa_cases.json'))
    parser.add_argument('--omni',type=Path,default=Path('E:/WK/run/m4-full-sweep-20261018/omni_pages.json'))
    parser.add_argument('--apply',action='store_true');parser.add_argument('--target-system-id')
    parser.add_argument('--asset-destination',type=Path);parser.add_argument('--bind-target-principal',action='store_true')
    return parser.parse_args(argv)


if __name__=='__main__':
    args=arguments()
    if args.command=='verify':
        manifest,_=verify_bundle(args.output);print(json.dumps({'status':'BUNDLE_INTEGRITY_VERIFIED','scope_documents':manifest['scope']['documents'],'scope_vectors':manifest['scope']['vector_rows']}))
    else:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(import_bundle(args) if args.command=='import' else export_bundle(args))
