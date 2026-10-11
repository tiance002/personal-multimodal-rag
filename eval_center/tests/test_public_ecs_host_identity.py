"""External readonly ECS metadata; SIMULATED index/Scope/SQL fixtures, no DB I/O."""
from copy import deepcopy
from dataclasses import asdict,replace
from pathlib import Path
import hashlib,json
import pytest
from sqlalchemy.engine import make_url
from backend.app.domain.scope import Scope
from eval_center.tests.test_verified_index import identity as legacy_identity
from eval_center.verified_index import (
    ECSReadOnlyDatabaseProfile,IndexReuseRejected,validate_index_identity,
    require_read_only_connection,read_only_database_url,
)

EVIDENCE=Path('var/reports/v6-ecs-resume-r1/ecs-db-missing-identity-readonly1.json')
EVIDENCE_SHA='5ff1189a242d4ff6a556d6914f13c07cbc1163218528daf60280c27324fbe662'


def host_profile():
    return ECSReadOnlyDatabaseProfile.from_host_metadata_receipt(EVIDENCE,expected_sha256=EVIDENCE_SHA)


def host_index():
    profile=host_profile();scope=Scope.from_ids(['SIMULATED-kb'],['SIMULATED-doc'])
    expected=legacy_identity()
    for f in ('container_id','container_name','volume_name'):expected.pop(f)
    for f in ('database','port','system_identifier','database_oid','schema_revision',
              'deployment_kind','data_directory','service_name','postgres_executable'):
        expected[f]=getattr(profile,f)
    expected['scope']={'kb':sorted(scope.knowledge_base_ids),'documents':sorted(scope.document_ids)}
    return profile,scope,expected


def test_approved_external_metadata_builds_host_profile_without_docker_or_ready_flags():
    p=host_profile();p.validate()
    assert p.deployment_kind=='HOST_NATIVE_POSTGRESQL'
    assert p.port==5432 and p.system_identifier=='7687882228252794550' and p.database_oid==19805
    assert p.schema_revision=='0022_memory_vector_index' and p.database=='rag_p7_r1'
    assert p.data_directory=='/var/lib/postgresql/16/main'
    assert p.service_name=='postgresql@16-main.service' and p.postgres_executable=='/usr/lib/postgresql/16/bin/postgres'
    assert p.container_id is p.container_name is p.volume_name is None
    assert p.evidence_sha256==EVIDENCE_SHA
    assert not {'scope','ready_state','database_read_only'}&set(asdict(p))


def test_host_identity_accepts_only_complete_simulated_index_and_scope():
    p,s,e=host_index()
    proof=validate_index_identity(e,deepcopy(e),database_profile=p,scope=s)
    assert proof['status']=='VERIFIED_READ_ONLY_REUSE'
    assert {'deployment_kind','data_directory','service_name','postgres_executable','system_identifier',
        'database_oid','scope'}<=set(proof['verified_fields'])
    assert not {'container_id','container_name','volume_name'}&set(proof['verified_fields'])
    nulls={**e,'container_id':None,'container_name':None,'volume_name':None}
    validate_index_identity(nulls,deepcopy(nulls),database_profile=p,scope=s)
    with pytest.raises(IndexReuseRejected,match='scope'):validate_index_identity(e,deepcopy(e),database_profile=p)
    with pytest.raises(IndexReuseRejected):validate_index_identity(e,deepcopy(e))
    assert validate_index_identity(legacy_identity(),legacy_identity())['status']=='VERIFIED_READ_ONLY_REUSE'


@pytest.mark.parametrize('field',['database','port','system_identifier','database_oid','schema_revision',
    'deployment_kind','data_directory','service_name','postgres_executable','scope','embedding_digest',
    'index_fingerprint','index_counts','ready_state','database_read_only','corpus_sha256'])
def test_host_identity_missing_or_mismatched_field_stays_rejected(field):
    p,s,e=host_index();a=deepcopy(e);a.pop(field)
    with pytest.raises(IndexReuseRejected):validate_index_identity(e,a,database_profile=p,scope=s)
    a=deepcopy(e);a[field]='SIMULATED-wrong'
    with pytest.raises(IndexReuseRejected):validate_index_identity(e,a,database_profile=p,scope=s)
    if field in ('database','port','system_identifier','database_oid','schema_revision','deployment_kind',
                 'data_directory','service_name','postgres_executable'):
        e[field]=a[field]
        with pytest.raises(IndexReuseRejected):validate_index_identity(e,a,database_profile=p,scope=s)


@pytest.mark.parametrize('field,value',[('container_id','7'*64),('container_name','rag-eval'),
    ('volume_name','SIMULATED-volume'),('data_directory',None),('data_directory','UNKNOWN'),
    ('data_directory','/var/lib/postgresql/../other'),('service_name',None),('service_name','other.service'),
    ('postgres_executable',None),('postgres_executable','/usr/bin/python'),('deployment_kind','UNKNOWN')])
def test_host_profile_forbids_fake_docker_and_unknown_instance_fields(field,value):
    with pytest.raises(IndexReuseRejected):replace(host_profile(),**{field:value}).validate()


def test_local_p4_system_id_and_unknown_scope_cannot_satisfy_ecs_identity():
    p,s,e=host_index();a=deepcopy(e)
    a['system_identifier']='7691227493754040358'
    with pytest.raises(IndexReuseRejected,match='system_identifier'):validate_index_identity(e,a,database_profile=p,scope=s)
    e['system_identifier']=a['system_identifier']
    with pytest.raises(IndexReuseRejected,match='system_identifier'):validate_index_identity(e,a,database_profile=p,scope=s)


@pytest.mark.parametrize('field',['container_id','container_name','volume_name'])
def test_host_index_rejects_fabricated_docker_placeholders(field):
    p,s,e=host_index();e[field]='SIMULATED-fake-docker'
    with pytest.raises(IndexReuseRejected,match='not_applicable'):validate_index_identity(e,deepcopy(e),database_profile=p,scope=s)


def test_host_endpoint_is_exact_and_driver_overrides_removed():
    p=host_profile()
    url=make_url(read_only_database_url('postgresql+psycopg://SIMULATED:x@127.0.0.1:5432/postgres?host=evil.invalid&port=25437&dbname=other',
        'rag_p7_r1',database_profile=p))
    assert url.host=='127.0.0.1' and url.port==5432 and url.database=='rag_p7_r1'
    assert url.query['options']=='-c default_transaction_read_only=on'
    assert set(url.query)=={'options','application_name'}
    with pytest.raises(IndexReuseRejected):read_only_database_url('postgresql+psycopg://SIMULATED@127.0.0.1:25437/postgres',
        'rag_p7_r1',database_profile=p)


class Result:
    def __init__(self,row):self.row=row
    def one(self):return self.row


class SimulatedConnection:
    def __init__(self,row,*,database='rag_p7_r1',readonly='on'):
        self.row=row;self.database=database;self.readonly=readonly;self.statements=[]
    def execute(self,sql):
        self.statements.append(str(sql))
        return Result((self.database,self.readonly) if len(self.statements)==1 else self.row)


def connection_identity(p):return (p.system_identifier,p.database_oid,p.schema_revision,p.data_directory,p.port)


def test_host_connection_rechecks_full_instance_and_fresh_readonly_transaction():
    p=host_profile();c=SimulatedConnection(connection_identity(p))
    require_read_only_connection(c,p.database,database_profile=p)
    assert len(c.statements)==2 and "current_setting('data_directory')" in c.statements[1]
    assert "current_setting('port')::integer" in c.statements[1]
    # Metadata transaction 'on' must never substitute for the index connection.
    c=SimulatedConnection(connection_identity(p),readonly='off')
    with pytest.raises(IndexReuseRejected,match='database_read_only'):
        require_read_only_connection(c,p.database,database_profile=p)
    assert len(c.statements)==1


@pytest.mark.parametrize('slot',range(5))
def test_host_connection_wrong_system_oid_migration_directory_or_port(slot):
    p=host_profile();row=list(connection_identity(p));row[slot]='SIMULATED-other'
    with pytest.raises(IndexReuseRejected):require_read_only_connection(SimulatedConnection(tuple(row)),p.database,database_profile=p)


def test_missing_connection_columns_cannot_be_silently_zipped_away():
    p=host_profile()
    with pytest.raises(IndexReuseRejected,match='connected_instance_fields'):
        require_read_only_connection(SimulatedConnection(connection_identity(p)[:3]),p.database,database_profile=p)


def test_metadata_hash_and_missing_process_evidence_rejected(tmp_path):
    with pytest.raises(IndexReuseRejected,match='ecs_metadata_evidence'):
        ECSReadOnlyDatabaseProfile.from_host_metadata_receipt(EVIDENCE,expected_sha256='f'*64)
    outer=json.loads(EVIDENCE.read_text(encoding='utf-8'));inner=json.loads(outer['stdout'])
    inner['native_postgres_process']['pid']+=1;outer['stdout']=json.dumps(inner)
    path=tmp_path/'SIMULATED-tampered-receipt.json';path.write_text(json.dumps(outer),encoding='utf-8')
    with pytest.raises(IndexReuseRejected,match='ecs_metadata_evidence'):
        ECSReadOnlyDatabaseProfile.from_host_metadata_receipt(path,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
