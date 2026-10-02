"""Explicit Windows-native release plans. Default actions are offline and read only."""
from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from urllib.parse import unquote, urlsplit


class ReleaseError(Exception):
    """Only fixed, non-secret diagnostic codes may escape this module."""


FIELDS = {'source_root','python','native_python','node','docker','db_container','tessdata_dir',
          'docker_endpoint','docker_config_root','db_container_id','db_system_identifier','database_oid',
          'database_name','db_port','storage_root','backup_root','api_port','ui_port','purpose','migration_revision'}
PATH_FIELDS = {'source_root','python','native_python','node','docker','docker_config_root','tessdata_dir','storage_root','backup_root'}
NATIVE_VERSIONS = {'beautifulsoup4':'4.15.0','python-docx':'1.2.0','defusedxml':'0.7.1'}
FORMATS = ['TXT','MD','PDF','HTML','DOCX','XLSX']
MAX_TARGET_JSON = 64 * 1024
MAX_FRONTEND_LOCK = 4 * 1024 * 1024
MAX_BACKUP_MANIFEST = 16 * 1024 * 1024

MODEL_NAMES = {'chat':'qwen3.5:4b','embedding':'bge-m3:latest'}
ENV_NAMES = {'SYSTEMROOT','WINDIR','TEMP','TMP'}


def minimal_environment(inherited):
    return {name:inherited.get(name) for name in ENV_NAMES if inherited.get(name) is not None}


def fail(code):
    raise ReleaseError(code)


def absolute_path(value):
    if not isinstance(value,str) or not value or '\x00' in value:
        fail('ABSOLUTE_PATH_REQUIRED')
    p = Path(value)
    if not p.is_absolute() or '..' in p.parts:
        fail('ABSOLUTE_PATH_REQUIRED')
    # Reject junctions/symlinks before resolution; they obscure the selected identity.
    for item in (p, *p.parents):
        if item.is_symlink() or (item.exists() and getattr(item.stat(),'st_file_attributes',0) & 0x400):
            fail('REPARSE_PATH_UNSUPPORTED')
    return p.resolve()


def overlap(a,b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def source_identity(root):
    # Explicit code/config scopes, never .env, private var/, .git or sessions.
    root = Path(root)
    files = [root/'pyproject.toml',root/'frontend/package-lock.json']
    for rel, suffixes in [('backend/app',{'.py'}),('alembic/versions',{'.py'}),('frontend/src',{'.ts','.tsx','.css'})]:
        folder = root/rel
        if folder.exists():
            files.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in suffixes)
    for name in ['native_release.py','native_release_vite.config.mjs','start_backend.py','backup.ps1','restore.ps1','verify-release.ps1']:
        p = root/'scripts'/name
        if p.exists(): files.append(p)
    files.append(root/'frontend/dist/index.html')
    records = []
    for p in sorted(set(files)):
        if not p.is_file(): fail('SOURCE_OR_FRONTEND_ARTIFACT_MISSING')
        absolute_path(str(p))
        records.append([p.relative_to(root).as_posix(),sha256(p)])
    # Include all existing compiled UI assets so a changed bundle invalidates the plan.
    dist = root/'frontend/dist'
    for p in sorted(dist.rglob('*')):
        if p.is_file() and p not in files:
            absolute_path(str(p)); records.append([p.relative_to(root).as_posix(),sha256(p)])
    return hashlib.sha256(json.dumps(sorted(records),separators=(',',':')).encode()).hexdigest()


def target_hash(target):
    return hashlib.sha256(json.dumps({k:v for k,v in target.items() if k!='target_id'},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_fields(values):
    if set(values) != FIELDS: fail('TARGET_FIELDS_INVALID')
    for field in PATH_FIELDS: absolute_path(values[field])
    if not isinstance(values['database_name'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',values['database_name']):
        fail('DATABASE_NAME_INVALID')
    if not isinstance(values['db_container'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',values['db_container']):
        fail('EXPLICIT_DATABASE_CONTAINER_REQUIRED')
    if not isinstance(values['docker_endpoint'],str) or not re.fullmatch(r'npipe:////\./pipe/[A-Za-z0-9_.-]{1,128}',values['docker_endpoint']):
        fail('EXPLICIT_LOCAL_DOCKER_ENDPOINT_REQUIRED')
    if not isinstance(values['db_container_id'],str) or not re.fullmatch(r'[0-9a-f]{64}',values['db_container_id']):
        fail('FULL_DATABASE_CONTAINER_ID_REQUIRED')
    if not isinstance(values['db_system_identifier'],str) or not re.fullmatch(r'[1-9][0-9]{0,19}',values['db_system_identifier']):
        fail('OBSERVED_DATABASE_CLUSTER_ID_REQUIRED')
    if type(values['database_oid']) is not int or not 0 < values['database_oid'] < 2**32:
        fail('OBSERVED_DATABASE_OID_REQUIRED')
    if values['purpose'] not in {'release','acceptance','restore'}: fail('PURPOSE_REQUIRED')
    if values['purpose']=='release' and (values['database_name'] in {'rag','postgres','template0','template1'} or
            re.search(r'(^|_)(test|acceptance|integration|restore)($|_)',values['database_name'])):
        fail('RELEASE_DATABASE_REQUIRES_OWNER_SELECTION')
    for field in ['db_port','api_port','ui_port']:
        if type(values[field]) is not int or not 1 <= values[field] <= 65535: fail('PORT_INVALID')
    if len({values['db_port'],values['api_port'],values['ui_port']})!=3: fail('PORTS_MUST_DIFFER')
    if values['api_port'] in {18086,14186} or values['ui_port'] in {18086,14186}: fail('PROTECTED_SERVICE_PORT')
    if not isinstance(values['migration_revision'],str) or not re.fullmatch(r'[a-z0-9_]{1,64}',values['migration_revision']):
        fail('MIGRATION_REVISION_INVALID')
    source,storage,backups = [absolute_path(values[k]) for k in ['source_root','storage_root','backup_root']]
    if overlap(source,storage) or overlap(source,backups) or overlap(storage,backups):
        fail('SOURCE_STORAGE_BACKUP_MUST_BE_DISJOINT')


def select_target(values):
    validate_fields(values)
    target = {k:(str(absolute_path(v)) if k in PATH_FIELDS else v) for k,v in values.items()}
    target.update(schema_version=2,source_sha256=source_identity(target['source_root']))
    target['target_id'] = target_hash(target)
    return target


def validate_target(target):
    if not isinstance(target,dict) or set(target) != FIELDS|{'schema_version','source_sha256','target_id'}:
        fail('TARGET_FIELDS_INVALID')
    validate_fields({k:target[k] for k in FIELDS})
    if target['schema_version']!=2 or not re.fullmatch(r'[0-9a-f]{64}',str(target['source_sha256'])):
        fail('TARGET_SCHEMA_INVALID')
    if target.get('target_id')!=target_hash(target): fail('TARGET_IDENTITY_MISMATCH')


def read_json(path, *, max_bytes=None):
    max_bytes=MAX_TARGET_JSON if max_bytes is None else max_bytes
    path=absolute_path(str(path))
    if path.suffix.lower()!='.json' or path.stat().st_size>max_bytes: fail('JSON_INPUT_INVALID')
    def unique(items):
        result={}
        for key,value in items:
            if key in result: fail('JSON_DUPLICATE_FIELD')
            result[key]=value
        return result
    try:
        with path.open('rb') as stream: raw=stream.read(max_bytes+1)
        if len(raw)>max_bytes: fail('JSON_INPUT_INVALID')
        return json.loads(raw.decode('utf-8-sig'),object_pairs_hook=unique)
    except (UnicodeError,json.JSONDecodeError): fail('JSON_INPUT_INVALID')


def assert_selection(target,name,storage):
    validate_target(target)
    if name!=target['database_name'] or absolute_path(storage)!=Path(target['storage_root']):
        fail('DATABASE_STORAGE_SELECTION_MISMATCH')


def runtime_versions(executable):
    executable=Path(executable)
    if not executable.is_file() or executable.suffix.lower()!='.exe': fail('EXECUTABLE_MISSING')
    venv=executable.parent.parent
    cfg=venv/'pyvenv.cfg'
    if not cfg.is_file(): fail('WINDOWS_VENV_REQUIRED')
    match=re.search(r'^version\s*=\s*(\d+)\.(\d+)\.(\d+)\s*$',cfg.read_text(encoding='utf-8'),re.M)
    if not match or tuple(map(int,match.groups())) < (3,11,0): fail('PYTHON_VERSION_UNSUPPORTED')
    versions={}
    for p in (venv/'Lib/site-packages').glob('*.dist-info/METADATA'):
        metadata=Parser().parsestr(p.read_text(encoding='utf-8'))
        name=re.sub(r'[-_.]+','-',str(metadata.get('Name','')).lower())
        version=str(metadata.get('Version',''))
        if not re.fullmatch(r'[0-9][A-Za-z0-9.+_-]{0,63}',version): fail('DEPENDENCY_METADATA_INVALID')
        if name in versions: fail('DEPENDENCY_DUPLICATE_METADATA')
        versions[name]=version
    return '.'.join(match.groups()),versions


def expected_main_versions(source):
    import tomllib
    parsed=tomllib.loads((Path(source)/'pyproject.toml').read_text(encoding='utf-8-sig'))
    expected={}
    for requirement in parsed['project']['dependencies']:
        match=re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9.+_-]+)',requirement)
        if not match: fail('UNPINNED_MAIN_DEPENDENCY')
        expected[re.sub(r'[-_.]+','-',match[1].lower())]=match[2]
    for required in ['pymupdf','openpyxl','defusedxml','pillow','fastapi']:
        if required not in expected: fail('PARSER_DEPENDENCY_DECLARATION_MISSING')
    return expected


def migration_head(source):
    revisions={}; parents=set()
    for path in (Path(source)/'alembic/versions').glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        values={}
        for node in tree.body:
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                names=node.targets if isinstance(node,ast.Assign) else [node.target]
                for name in names:
                    if isinstance(name,ast.Name) and name.id in {'revision','down_revision'}:
                        values[name.id]=ast.literal_eval(node.value)
        if 'revision' in values:
            revisions[values['revision']]=path
            parent=values.get('down_revision')
            if isinstance(parent,str): parents.add(parent)
            elif isinstance(parent,tuple): parents.update(parent)
    heads=set(revisions)-parents
    if len(heads)!=1: fail('MIGRATION_HEAD_AMBIGUOUS')
    return heads.pop()


def offline_preflight(target):
    validate_target(target)
    source=Path(target['source_root'])
    if source_identity(source)!=target['source_sha256']: fail('SOURCE_OR_UI_CHANGED_RESELECT_TARGET')
    if migration_head(source)!=target['migration_revision']: fail('SOURCE_MIGRATION_MISMATCH')
    pv,main=runtime_versions(target['python']); nv,native=runtime_versions(target['native_python'])
    expected=expected_main_versions(source)
    for name,version in expected.items():
        if main.get(name)!=version: fail('MAIN_DEPENDENCY_VERSION_MISMATCH')
    for name,version in NATIVE_VERSIONS.items():
        if native.get(name)!=version: fail('NATIVE_DEPENDENCY_VERSION_MISMATCH')
    if not all(name in native for name in ['lxml','soupsieve']): fail('NATIVE_TRANSITIVE_DEPENDENCY_MISSING')
    languages={}
    for name in ['eng','chi_sim']:
        p=Path(target['tessdata_dir'])/(name+'.traineddata')
        if not p.is_file() or p.stat().st_size==0: fail('OCR_LANGUAGE_RESOURCE_MISSING')
        absolute_path(str(p)); languages[name]=sha256(p)
    for field in ['node','docker']:
        p=Path(target[field])
        if not p.is_file() or p.suffix.lower()!='.exe': fail('EXECUTABLE_MISSING')
    for rel in ['frontend/node_modules/vite/bin/vite.js','scripts/start_backend.py','scripts/native_release_vite.config.mjs']:
        if not (source/rel).is_file(): fail('LAUNCHER_OR_FRONTEND_RUNTIME_MISSING')
    lock=read_json(source/'frontend/package-lock.json',max_bytes=MAX_FRONTEND_LOCK)
    if lock.get('lockfileVersion') not in {2,3}: fail('FRONTEND_LOCK_UNSUPPORTED')
    if not Path(target['docker_config_root']).is_dir(): fail('EXPLICIT_DOCKER_CONFIG_DIRECTORY_REQUIRED')
    if not Path(target['backup_root']).is_dir(): fail('BACKUP_ROOT_MUST_EXIST')
    storage=Path(target['storage_root'])
    if target['purpose']=='restore':
        if storage.exists(): fail('RESTORE_STORAGE_MUST_BE_NEW')
        if not storage.parent.is_dir(): fail('RESTORE_STORAGE_PARENT_MISSING')
    elif not storage.is_dir() or not (storage/'objects').is_dir():
        fail('EXPLICIT_EXISTING_STORAGE_REQUIRED')
    return {'status':'STATIC_PASS_RUNTIME_NOT_RUN','formats':FORMATS,'automatic_doc':False,
            'python_version':pv,'native_python_version':nv,'main_versions':expected,
            'native_versions':{k:native[k] for k in sorted(NATIVE_VERSIONS.keys()|{'lxml','soupsieve'})},
            'tessdata_sha256':languages,'source_sha256':target['source_sha256'],
            'executables_sha256':{field:sha256(target[field]) for field in ['python','native_python','node','docker']},
            'model_names':MODEL_NAMES,'model_runtime':'NOT_RUN','ports':'NOT_RUN','database':'NOT_RUN',
            'node_docker_runtime':'NOT_RUN','dependency_imports':'NOT_RUN'}


def start_commands(target):
    root=Path(target['source_root'])
    return {'api':[target['python'],'-I','-B',str(root/'scripts/start_backend.py'),'--no-env'],
            'ui':[target['node'],str(root/'frontend/node_modules/vite/bin/vite.js'),'preview',
                  '--config',str(root/'scripts/native_release_vite.config.mjs'),
                  '--host','127.0.0.1','--port',str(target['ui_port']),'--strictPort']}


def child_environment(target,inherited,*,database_url=None):
    env=minimal_environment(inherited)
    env.update(RAG_HOST='127.0.0.1',RAG_PORT=str(target['api_port']),
        RAG_STORAGE_ROOT=target['storage_root'],RAG_NATIVE_TABLE_PYTHON=target['native_python'],
        RAG_TESSDATA_DIR=target['tessdata_dir'],RAG_INLINE_INGESTION='true',
        RAG_CLOUD_ENABLED='false',RAG_PREFER_CLOUD='false',RAG_CLOUD_FALLBACK_ENABLED='false',
        RAG_LANGFUSE_ENABLED='false',RAG_LANGFUSE_CAPTURE_CONTENT='false',
        RAG_LOCAL_QUERY_ENABLED='false',RAG_LOCAL_ANSWER_ENABLED='true',RAG_RETRIEVAL_MODE='adaptive',
        OLLAMA_BASE_URL='http://127.0.0.1:11434',OLLAMA_CHAT_MODEL=MODEL_NAMES['chat'],
        OLLAMA_EMBEDDING_MODEL=MODEL_NAMES['embedding'],RAG_NATIVE_API_PORT=str(target['api_port']),
        RAG_NATIVE_UI_PORT=str(target['ui_port']),PYTHONDONTWRITEBYTECODE='1')
    if database_url is not None:
        env['RAG_DATABASE_URL']=database_url.replace('postgresql://','postgresql+psycopg://',1)
    return env


def validate_dsn(target,url):
    try:
        parsed=urlsplit(url.replace('postgresql+psycopg://','postgresql://',1))
        valid=(parsed.scheme=='postgresql' and parsed.hostname=='127.0.0.1' and
               parsed.port==target['db_port'] and unquote(parsed.path)=='/'+target['database_name'] and
               parsed.username and not parsed.query and not parsed.fragment)
    except (TypeError,ValueError): valid=False
    if not valid: fail('RUNTIME_DATABASE_IDENTITY_MISMATCH')
    return parsed


def runtime_dsn(target):
    url=os.environ.get('RAG_NATIVE_DATABASE_URL','')
    if not url: fail('RUNTIME_DATABASE_URL_REQUIRED')
    validate_dsn(target,url)
    return url.replace('postgresql+psycopg://','postgresql://',1)


def require_confirmation(target,confirmation):
    if confirmation!=target['target_id']: fail('EXACT_TARGET_CONFIRMATION_REQUIRED')


def check_port(port):
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'): sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.bind(('127.0.0.1',port))
    except OSError: fail('LOOPBACK_PORT_UNAVAILABLE')


def validate_objects(root,rows):
    root=Path(root)
    for key,digest in rows:
        if not isinstance(key,str) or not re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{64}',key):
            fail('DATABASE_STORAGE_KEY_INVALID')
        p=absolute_path(str(root/key))
        if not p.is_file() or sha256(p)!=digest: fail('DATABASE_STORAGE_OBJECT_MISMATCH')


def connect_readonly(url):
    import psycopg
    connection=psycopg.connect(url,autocommit=True,connect_timeout=5)
    connection.execute('SET default_transaction_read_only=on')
    return connection


def validate_database_identity(target, observed):
    expected={'database':target['database_name'],'system_identifier':target['db_system_identifier'],'database_oid':target['database_oid']}
    if observed!=expected: fail('OBSERVED_DATABASE_IDENTITY_MISMATCH')


def observe_database_identity(connection):
    row=connection.execute("SELECT current_database(), (SELECT system_identifier::text FROM pg_control_system()), (SELECT oid FROM pg_database WHERE datname=current_database())").fetchone()
    return {'database':row[0],'system_identifier':str(row[1]),'database_oid':row[2]}


def check_database(target,url):
    with connect_readonly(url) as connection:
        validate_database_identity(target,observe_database_identity(connection))
        revision=connection.execute('SELECT version_num FROM alembic_version').fetchall()
        if revision!=[(target['migration_revision'],)]: fail('DATABASE_MIGRATION_MISMATCH')
        rows=connection.execute('SELECT storage_key, source_sha256 FROM document_versions').fetchall()
        normalized=connection.execute('SELECT normalized_content_key, normalized_content_sha256 FROM document_versions WHERE normalized_content_key IS NOT NULL').fetchall()
        assets=connection.execute('SELECT storage_key FROM document_assets WHERE storage_key IS NOT NULL').fetchall()
        asset_rows=[(row[0],row[0].rsplit('/',1)[-1]) for row in assets]
        validate_objects(target['storage_root'],rows+normalized+asset_rows)
    return len(rows)+len(normalized)+len(asset_rows)


def owned_foreground(command,cwd,env):
    # No process lookup, background registry or stop command. Only this handle is owned.
    process=subprocess.Popen(command,cwd=cwd,env=env,shell=False,stdin=None,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def verify_runtime_imports(target):
    # Only explicit execution probes imports, never the default offline path.
    for executable,modules in [(target['python'],['fastapi','uvicorn','sqlalchemy','psycopg','fitz','openpyxl','PIL','defusedxml']),
                               (target['native_python'],['bs4','docx','lxml','soupsieve','defusedxml'])]:
        code='import importlib; [importlib.import_module(n) for n in '+repr(modules)+']'
        result=subprocess.run([executable,'-I','-B','-c',code],env=minimal_environment(os.environ),
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False,timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: fail('RUNTIME_DEPENDENCY_IMPORT_FAILED')


def require_execution_python(target):
    if Path(sys.executable).resolve()!=Path(target['python']):
        fail('EXECUTION_MUST_USE_SELECTED_PYTHON')


def verify_node_version(target):
    result=subprocess.run([target['node'],'--version'],env=minimal_environment(os.environ),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,shell=False,timeout=10,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    match=re.fullmatch(rb'v(\d+)\.\d+\.\d+\s*',result.stdout or b'')
    if result.returncode or not match or int(match[1])<22: fail('NODE_VERSION_UNSUPPORTED')


def execute_start(target,component):
    if target['purpose']=='restore': fail('RESTORE_TARGET_CANNOT_START_UNTIL_RESELECTED')
    verify_runtime_imports(target)
    verify_node_version(target)
    url=runtime_dsn(target)
    check_database(target,url)
    check_port(target['api_port'] if component=='api' else target['ui_port'])
    env=child_environment(target,os.environ,database_url=url if component=='api' else None)
    code=owned_foreground(start_commands(target)[component],target['source_root'],env)
    if code: fail('OWNED_PROCESS_EXITED_WITH_ERROR')
    return {'status':'OWNED_PROCESS_EXITED_NOT_ACCEPTANCE','component':component}


def backup_destination(target,value):
    p=absolute_path(value)
    if p.exists(): fail('BACKUP_DESTINATION_ALREADY_EXISTS')
    if p.parent!=Path(target['backup_root']): fail('BACKUP_DESTINATION_OUTSIDE_SELECTED_ROOT')
    if overlap(p,Path(target['storage_root'])) or overlap(p,Path(target['source_root'])):
        fail('BACKUP_DESTINATION_OVERLAPS_SOURCE')
    return p


def pg_environment(target,url):
    parsed=validate_dsn(target,url)
    env=minimal_environment(os.environ)
    env.update(PGHOST='127.0.0.1',PGPORT=str(target['db_port']),PGDATABASE=target['database_name'],
               PGUSER=unquote(parsed.username),PGPASSWORD=unquote(parsed.password or ''),
               PGPASSFILE=os.devnull,PGCONNECT_TIMEOUT='5')
    return env


def docker_command(target, arguments):
    return [target['docker'],'--host',target['docker_endpoint'],'--config',target['docker_config_root'],*arguments]


def docker_environment(target,url=None):
    env=pg_environment(target,url) if url is not None else minimal_environment(os.environ)
    env.update(PGPORT='5432',PGPASSFILE='/dev/null')
    return env


def container_exec_command(target,program,flags):
    arguments=['exec','-i']
    for name in ['PGHOST','PGPORT','PGDATABASE','PGUSER','PGPASSWORD','PGPASSFILE','PGCONNECT_TIMEOUT']:
        arguments.extend(['-e',name])
    arguments.extend([target['db_container_id'],program,*flags])
    return docker_command(target,arguments)


def check_container_mapping(target,url=None):
    # Only explicitly formatted non-secret fields; never inspect .Config.Env.
    template='{"Id":{{json .Id}},"Name":{{json .Name}},"Ports":{{json .NetworkSettings.Ports}}}'
    command=docker_command(target,['inspect','--format',template,target['db_container_id']])
    result=subprocess.run(command,env=docker_environment(target,url),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,shell=False,timeout=15,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: fail('DATABASE_CONTAINER_INSPECT_FAILED')
    try:
        info=json.loads(result.stdout)
        identity=(info.get('Id')==target['db_container_id'] and info.get('Name')=='/'+target['db_container'])
        bindings=info.get('Ports',{}).get('5432/tcp') or []
        valid=identity and any(x.get('HostIp')=='127.0.0.1' and x.get('HostPort')==str(target['db_port']) for x in bindings)
    except (ValueError,TypeError,AttributeError): valid=False
    if not valid: fail('DATABASE_CONTAINER_IDENTITY_OR_MAPPING_MISMATCH')


def check_container_database_identity(target,url):
    sql="SELECT json_build_object('database',current_database(),'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()))"
    command=container_exec_command(target,'psql',['-X','-A','-t','-v','ON_ERROR_STOP=1','-c',sql])
    result=subprocess.run(command,env=docker_environment(target,url),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,shell=False,timeout=15,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or len(result.stdout or b'')>4096: fail('CONTAINER_DATABASE_IDENTITY_CHECK_FAILED')
    try: observed=json.loads(result.stdout)
    except (ValueError,TypeError): fail('CONTAINER_DATABASE_IDENTITY_CHECK_FAILED')
    validate_database_identity(target,observed)


def run_pg(program,flags,target,url,*,output=None,input_file=None):
    command=container_exec_command(target,program,flags)
    from contextlib import ExitStack
    with ExitStack() as stack:
        stdout=stack.enter_context(Path(output).open('xb')) if output else subprocess.DEVNULL
        stdin=stack.enter_context(Path(input_file).open('rb')) if input_file else subprocess.DEVNULL
        result=subprocess.run(command,env=docker_environment(target,url),stdin=stdin,stdout=stdout,
                              stderr=subprocess.DEVNULL,shell=False,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: fail('POSTGRES_TOOL_FAILED')


def backup_module(target):
    spec=importlib.util.spec_from_file_location('native_backup_manifest',Path(target['source_root'])/'backend/app/application/backup.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def safe_storage_tree(root):
    root=absolute_path(str(root))
    for p in root.rglob('*'): absolute_path(str(p))


def write_new_json(path,value,*,max_bytes=None):
    max_bytes=MAX_TARGET_JSON if max_bytes is None else max_bytes
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    if len(raw)>max_bytes: fail('JSON_OUTPUT_CAPACITY_EXCEEDED')
    with Path(path).open('xb') as stream: stream.write(raw)


def execute_backup(target,destination,quiescent):
    if not quiescent: fail('QUIESCENT_OWNER_CONFIRMATION_REQUIRED')
    if target['purpose']=='restore': fail('RESTORE_TARGET_CANNOT_BACKUP_UNTIL_RESELECTED')
    destination=backup_destination(target,str(destination))
    url=runtime_dsn(target)
    check_database(target,url)
    check_container_mapping(target,url)
    check_container_database_identity(target,url)
    safe_storage_tree(target['storage_root'])
    module=backup_module(target)
    destination.mkdir()  # Exclusive reservation; never overwrite or clean a failed attempt.
    try:
        dump=destination/'database.dump'
        run_pg('pg_dump',['--format=custom','--no-owner','--no-privileges'],target,url,output=dump)
        shutil.copytree(target['storage_root'],destination/'storage')
        safe_storage_tree(destination/'storage')
        manifest=module.build_backup_manifest(destination/'storage',dump,target['migration_revision'])
        manifest['native_target']=target
        manifest['readiness']=offline_preflight(target)
        manifest['quiescent_owner_confirmed']=True
        if module.validate_backup_manifest(manifest,destination/'storage',dump): fail('BACKUP_VERIFY_FAILED')
        write_new_json(destination/'manifest.pending.json',manifest,max_bytes=MAX_BACKUP_MANIFEST)
        verified_backup(target,destination,manifest_name='manifest.pending.json')
        write_new_json(destination/'manifest.json',manifest,max_bytes=MAX_BACKUP_MANIFEST)
        verified_backup(target,destination)
    except BaseException:
        write_new_json(destination/'failure.json',{'status':'INCOMPLETE_KEEP_FOR_REVIEW'})
        raise
    return {'status':'BACKUP_FILES_VERIFIED_RESTORE_NOT_RUN','target_id':target['target_id'],'backup_dir':str(destination)}


def validate_restore_target(target,original):
    validate_target(original)
    if target['purpose']!='restore' or target['database_name']==original['database_name']:
        fail('RESTORE_REQUIRES_DIFFERENT_EXPLICIT_DATABASE')
    storage=Path(target['storage_root'])
    if storage.exists() or overlap(storage,Path(original['storage_root'])):
        fail('RESTORE_STORAGE_MUST_BE_NEW')
    if target['db_system_identifier']==original['db_system_identifier'] and target['database_oid']==original['database_oid']:
        fail('RESTORE_REQUIRES_DISTINCT_OBSERVED_DATABASE_IDENTITY')
    if target['migration_revision']!=original['migration_revision']: fail('RESTORE_MIGRATION_MISMATCH')


def require_empty_database(connection):
    count=connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','f','S')").fetchone()[0]
    if count: fail('RESTORE_DATABASE_MUST_BE_EMPTY')


def verified_backup(target,directory,*,manifest_name='manifest.json'):
    directory=absolute_path(str(directory))
    if (directory/'failure.json').exists(): fail('BACKUP_INCOMPLETE')
    if manifest_name not in {'manifest.json','manifest.pending.json'}: fail('BACKUP_MANIFEST_NAME_INVALID')
    manifest=read_json(directory/manifest_name,max_bytes=MAX_BACKUP_MANIFEST)
    original=manifest.get('native_target'); validate_target(original)
    dump=directory/'database.dump'; storage=directory/'storage'
    if manifest.get('database',{}).get('dump_file')!='database.dump': fail('BACKUP_DUMP_NAME_INVALID')
    safe_storage_tree(storage)
    module=backup_module(target)
    if module.validate_backup_manifest(manifest,storage,dump): fail('BACKUP_VERIFY_FAILED')
    if module.storage_entries(storage)!=manifest.get('storage'): fail('BACKUP_STORAGE_INVENTORY_MISMATCH')
    return original,dump,storage


def execute_restore(target,directory):
    original,dump,storage=verified_backup(target,directory)
    validate_restore_target(target,original)
    url=runtime_dsn(target)
    check_container_mapping(target,url)
    check_container_database_identity(target,url)
    with connect_readonly(url) as connection:
        validate_database_identity(target,observe_database_identity(connection))
        require_empty_database(connection)
    # copytree refuses existing destinations. No DROP, overwrite, rollback or cleanup.
    shutil.copytree(storage,target['storage_root'])
    run_pg('pg_restore',['--dbname',target['database_name'],'--exit-on-error','--single-transaction','--no-owner','--no-privileges'],target,url,input_file=dump)
    count=check_database(target,url)
    write_new_json(Path(target['storage_root'])/'native-restore-receipt.json',
                   {'status':'RESTORE_OBJECTS_VERIFIED_OWNER_REVIEW_REQUIRED','original_target_id':original['target_id'],
                    'restored_target_id':target['target_id'],'version_object_count':count})
    return {'status':'RESTORE_OBJECTS_VERIFIED_OWNER_REVIEW_REQUIRED','target_id':target['target_id']}


def native_gate_plan(target,args):
    restore=read_json(args.restore_target_file)
    assert_selection(restore,args.restore_database_name,args.restore_storage_root)
    offline_preflight(restore)
    validate_restore_target(restore,target)
    if restore['source_sha256']!=target['source_sha256']: fail('NATIVE_GATE_SOURCE_MISMATCH')
    destination=backup_destination(target,args.output_dir)
    tool=Path(target['source_root'])/'scripts/native_release.py'
    original_pair=['--target-file',str(absolute_path(str(args.target_file))),
                   '--database-name',target['database_name'],'--storage-root',target['storage_root']]
    restore_pair=['--target-file',str(absolute_path(str(args.restore_target_file))),
                  '--database-name',restore['database_name'],'--storage-root',restore['storage_root']]
    original_prefix=[target['python'],'-I','-B',str(tool)]
    restore_prefix=[restore['python'],'-I','-B',str(tool)]
    return {'status':'NATIVE_GATE_PLAN_REAL_ACCEPTANCE_NOT_RUN','target_id':target['target_id'],
            'restore_target_id':restore['target_id'],
            'commands':{'inspect':[*original_prefix,'inspect',*original_pair],
                        'api':[*original_prefix,'start',*original_pair,'--component','api'],
                        'ui':[*original_prefix,'start',*original_pair,'--component','ui'],
                        'backup':[*original_prefix,'backup',*original_pair,'--output-dir',str(destination)],
                        'restore':[*restore_prefix,'restore',*restore_pair,'--input-dir',str(destination)]},
            'execution_confirmations':{'start':target['target_id'],'backup':target['target_id'],'restore':restore['target_id']},
            'required_owner_stages':['review/freeze','start and real UI QA','final frozen regression',
                                    'quiescent backup','separately approved empty-target restore','rollback drill'],
            'note':'Commands default dry-run. Approve each live stage separately; use its corresponding DSN. No legacy gate or release PASS.'}


class SafeParser(argparse.ArgumentParser):
    def error(self,message): fail('ARGUMENTS_INVALID')


def parser():
    p=SafeParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True,parser_class=SafeParser)
    select=sub.add_parser('select',help='Print a non-secret target manifest; redirect to a new reviewed JSON file')
    for field in sorted(FIELDS):
        select.add_argument('--'+field.replace('_','-'),required=True,type=int if field.endswith('_port') or field=='database_oid' else str)
    for action in ['inspect','start','backup','restore','gate']:
        command=sub.add_parser(action,help='Offline/dry-run by default; explicit --execute permits live actions')
        command.add_argument('--target-file',required=True,type=Path)
        command.add_argument('--database-name',required=True)
        command.add_argument('--storage-root',required=True)
        if action not in {'inspect','gate'}:
            command.add_argument('--execute',action='store_true')
            command.add_argument('--confirm-target')
        if action=='start': command.add_argument('--component',required=True,choices=['api','ui'])
        if action=='backup':
            command.add_argument('--output-dir',required=True)
            command.add_argument('--quiescent-confirmed',action='store_true')
        if action=='restore': command.add_argument('--input-dir',required=True)
        if action=='gate':
            command.add_argument('--output-dir',required=True)
            command.add_argument('--restore-target-file',required=True,type=Path)
            command.add_argument('--restore-database-name',required=True)
            command.add_argument('--restore-storage-root',required=True)
    return p


def main(argv=None):
    try:
        args=parser().parse_args(argv)
        if args.action=='select':
            result=select_target({k:getattr(args,k) for k in FIELDS})
        else:
            target=read_json(args.target_file)
            assert_selection(target,args.database_name,args.storage_root)
            readiness=offline_preflight(target)
            result={'status':'DRY_RUN','action':args.action,'target':target,'readiness':readiness}
            if args.action=='gate': result=native_gate_plan(target,args)
            if args.action=='inspect': result['status']='STATIC_PASS_RUNTIME_NOT_RUN'
            if args.action=='start': result['commands']=start_commands(target)
            if args.action=='backup': result['output_dir']=str(backup_destination(target,args.output_dir))
            if args.action=='restore':
                original,_,_=verified_backup(target,args.input_dir)
                validate_restore_target(target,original)
                result['input_dir']=str(absolute_path(args.input_dir))
            if getattr(args,'execute',False):
                require_confirmation(target,args.confirm_target)
                require_execution_python(target)
                if args.action=='start': result=execute_start(target,args.component)
                elif args.action=='backup': result=execute_backup(target,Path(args.output_dir),args.quiescent_confirmed)
                elif args.action=='restore': result=execute_restore(target,args.input_dir)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0
    except ReleaseError as exc:
        print(json.dumps({'status':'BLOCKED','error_code':str(exc)}))
        return 2
    except (Exception,KeyboardInterrupt):
        print(json.dumps({'status':'BLOCKED','error_code':'NATIVE_RELEASE_OPERATION_FAILED'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
