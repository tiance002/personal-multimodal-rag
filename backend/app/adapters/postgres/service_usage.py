"""Durable ordinary-service token quota; prompts and credentials stay out.

Production PostgreSQL serializes all roles sharing a policy, across workers.
Reserved and unknown calls consume their complete input/output reservation.
No code here changes the historical evaluation ledger or grants.
"""
import hashlib
import re
import threading

import sqlalchemy as sa

from backend.app.ports.service_policy import ServiceModelPolicy, validate_tokens


metadata = sa.MetaData()
policies = sa.Table('service_model_policies', metadata,
    sa.Column('id', sa.String(100), primary_key=True),
    sa.Column('fingerprint', sa.String(64), nullable=False),
    sa.Column('state', sa.String(12), nullable=False),
    sa.CheckConstraint("state IN ('active','suspended')", name='service_policy_state_ck'))
calls = sa.Table('service_token_reservations', metadata,
    sa.Column('id', sa.String(64), primary_key=True),
    sa.Column('policy_id', sa.String(100), sa.ForeignKey('service_model_policies.id'), nullable=False),
    sa.Column('request_id', sa.String(200), nullable=False),
    sa.Column('role', sa.String(30), nullable=False),
    sa.Column('scope_sha256', sa.String(64), nullable=False),
    sa.Column('wire_sha256', sa.String(64), nullable=False),
    sa.Column('input_reserved', sa.BigInteger(), nullable=False),
    sa.Column('output_reserved', sa.BigInteger(), nullable=False),
    sa.Column('actual_input', sa.BigInteger(), nullable=True),
    sa.Column('actual_output', sa.BigInteger(), nullable=True),
    sa.Column('cost_reservation_id', sa.String(36), nullable=True),
    sa.Column('state', sa.String(12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint('policy_id', 'request_id', name='service_policy_request_ux'),
    sa.CheckConstraint("state IN ('reserved','settled','unknown','released')", name='service_token_state_ck'),
    sa.CheckConstraint('input_reserved > 0 AND output_reserved >= 0 AND '
                       '(actual_input IS NULL OR actual_input >= 0) AND '
                       '(actual_output IS NULL OR actual_output >= 0)', name='service_token_counts_ck'))
sa.Index('service_token_policy_state_idx', calls.c.policy_id, calls.c.state)


class ServiceQuotaDenied(ValueError):
    pass


class PostgresServiceTokenLedger:
    def __init__(self, engine):
        self.engine = engine
        # SQLite is an offline fixture only. Production protection is the DB lock.
        self._fixture_lock = threading.RLock()

    def _lock(self, connection, policy_id):
        if connection.dialect.name == 'postgresql':
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                               {'key': 'service-token-policy:' + policy_id})

    def reserve(self, policy, *, request_id, role, scope_sha256, wire_sha256, input_tokens, output_tokens):
        if type(policy) is not ServiceModelPolicy:
            raise ServiceQuotaDenied('SERVICE_POLICY_REQUIRED')
        validate_tokens(input_tokens, output_tokens)
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200 or role not in policy.roles
                or any(type(x) is not str or not re.fullmatch(r'[0-9a-f]{64}', x)
                       for x in (scope_sha256, wire_sha256))):
            raise ServiceQuotaDenied('SERVICE_REQUEST_IDENTITY_INVALID')
        identity = hashlib.sha256((policy.policy_id + '\0' + request_id).encode()).hexdigest()
        with self._fixture_lock, self.engine.begin() as connection:
            self._lock(connection, policy.policy_id)
            row = connection.execute(sa.select(policies).where(policies.c.id == policy.policy_id)).mappings().first()
            if row is None:
                connection.execute(policies.insert().values(id=policy.policy_id, fingerprint=policy.fingerprint, state='active'))
            elif row['fingerprint'] != policy.fingerprint:
                raise ServiceQuotaDenied('SERVICE_POLICY_CHANGED_REQUIRES_NEW_AUTHORIZATION')
            elif row['state'] != 'active':
                raise ServiceQuotaDenied('SERVICE_POLICY_SUSPENDED')
            if connection.execute(sa.select(calls.c.id).where(calls.c.id == identity)).first():
                # A completed, unknown or even proven unsent identity cannot resend.
                raise ServiceQuotaDenied('SERVICE_REQUEST_ALREADY_RESERVED')
            used = connection.execute(sa.select(sa.func.coalesce(sa.func.sum(sa.case(
                (calls.c.state == 'settled', calls.c.actual_input + calls.c.actual_output),
                (calls.c.state.in_(['reserved', 'unknown']), calls.c.input_reserved + calls.c.output_reserved),
                else_=0)), 0)).where(calls.c.policy_id == policy.policy_id)).scalar_one()
            if policy.token_limit is not None and int(used) + input_tokens + output_tokens > policy.token_limit:
                raise ServiceQuotaDenied('SERVICE_TOKEN_LIMIT_EXCEEDED')
            connection.execute(calls.insert().values(id=identity, policy_id=policy.policy_id,
                request_id=request_id, role=role, scope_sha256=scope_sha256, wire_sha256=wire_sha256,
                input_reserved=input_tokens, output_reserved=output_tokens, state='reserved'))
        return identity

    def link_cost(self, reservation, cost_reservation_id):
        with self.engine.begin() as connection:
            result = connection.execute(calls.update().where(calls.c.id == reservation,
                calls.c.state == 'reserved', calls.c.cost_reservation_id.is_(None))
                .values(cost_reservation_id=cost_reservation_id))
            if result.rowcount != 1:
                raise ServiceQuotaDenied('SERVICE_COST_LINK_INVALID')

    def check_before_send(self, policy, reservation):
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(calls.c.state, calls.c.cost_reservation_id,
                policies.c.fingerprint, policies.c.state.label('policy_state'))
                .join(policies, calls.c.policy_id == policies.c.id)
                .where(calls.c.id == reservation, policies.c.id == policy.policy_id)).mappings().first()
            if (row is None or row['state'] != 'reserved' or row['cost_reservation_id'] is None
                    or row['fingerprint'] != policy.fingerprint or row['policy_state'] != 'active'):
                raise ServiceQuotaDenied('SERVICE_DISPATCH_FENCE_DENIED')

    def finish(self, reservation, usage, *, sent):
        """Repeated finalization is idempotent; unknowns never release later."""
        violation = False
        with self._fixture_lock, self.engine.begin() as connection:
            policy_id = connection.execute(sa.select(calls.c.policy_id).where(calls.c.id == reservation)).scalar_one()
            self._lock(connection, policy_id)
            row = connection.execute(sa.select(calls).where(calls.c.id == reservation).with_for_update()).mappings().one()
            if row['state'] != 'reserved':
                return row['state']
            values = {'state': 'released' if not sent else 'unknown'}
            if sent and isinstance(usage, dict):
                i, o, total = (usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                valid = all(type(x) is int and x >= 0 for x in (i, o, total)) and i + o == total
                if valid and i <= row['input_reserved'] and o <= row['output_reserved']:
                    values.update(state='settled', actual_input=i, actual_output=o)
                elif valid:
                    # A supplier bound breach fences the entire shared policy.
                    # Retain at least the actual known total as occupied quota.
                    values.update(input_reserved=max(i, row['input_reserved']),
                                  output_reserved=max(o, row['output_reserved']), actual_input=i, actual_output=o)
                    connection.execute(policies.update().where(policies.c.id == policy_id).values(state='suspended'))
                    violation = True
            connection.execute(calls.update().where(calls.c.id == reservation).values(**values))
        if violation:
            raise ServiceQuotaDenied('SERVICE_USAGE_EXCEEDED_RESERVATION')
        return values['state']
