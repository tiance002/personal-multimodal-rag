"""One persistent local owner; no login, registration, roles or old-DB import."""
import uuid

from .history import LocalPrincipal


class LocalPrincipalStore:
    def __init__(self, lease_factory): self.leases=lease_factory

    async def bootstrap(self) -> LocalPrincipal:
        async with self.leases.lease() as guard:
            guard.require_verified();conn=guard.connection
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(20261013,700)')
                rows=await (await conn.execute("SELECT p.tenant_id,p.subject_id FROM port_local_principal p JOIN tenants t ON t.id=p.tenant_id WHERE p.slot='local' AND t.deleted_at IS NULL FOR UPDATE OF p,t")).fetchall()
                if rows:return LocalPrincipal(rows[0]['tenant_id'],rows[0]['subject_id'])
                exists=await (await conn.execute("SELECT slot FROM port_local_principal WHERE slot='local'")).fetchall()
                if exists:raise PermissionError('LOCAL_PRINCIPAL_TENANT_UNAVAILABLE')
                tenant=(await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('WeKnora local','personal-local'))).fetchall())[0]['id']
                subject='web_user:local:'+str(uuid.uuid4())
                await conn.execute("INSERT INTO port_local_principal(slot,tenant_id,subject_id) VALUES('local',%s,%s)",(tenant,subject))
                return LocalPrincipal(tenant,subject)
