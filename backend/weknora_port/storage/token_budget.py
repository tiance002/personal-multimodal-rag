"""P1 Token storage strategy. No independent sender or approval object."""
from dataclasses import asdict
import hashlib
import json
from ..models.types import ProviderError,TokenUsage
from ..models.usage_facts import ProviderUsageFacts


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def fingerprint(call):
    facts = call.bound_facts
    values = [call.provider, call.model, call.model_id, call.endpoint, call.capability,
        call.wire_sha256, call.wire_bytes, call.output_budget, call.context.session_id,
        call.context.scope.knowledge_base_ids, call.context.scope.document_ids,
        facts.config_sha if facts else None, facts.model_fingerprint if facts else None]
    return hashlib.sha256(encode(values).encode()).hexdigest()


def same_month_authority(stored, expected):
    """Quota-authority identity comparison with a controlled legacy compatibility.

    The quota identity is the stable approval identity (account/provider/limit/
    carry-in/approval_source). Legacy rows stored the old
    `<approval_source>:<config_sha>` spelling, so a config/meter version change
    would previously mint a conflict. This is a read-only comparison: it never
    rewrites the stored row, never resets a blocked month and never creates a new
    quota pool.
    """
    if stored == expected:
        return True
    return (isinstance(stored, str) and isinstance(expected, str) and bool(expected)
            and stored.startswith(expected + ':'))


class TokenBudgetStore:
    def __init__(self, gate):
        self.gate = gate
        self.db = gate.connection

    async def reserve(self, call):
        g = self.gate
        g._call(call)
        authority = g.token_authority
        if g.permissions is None or authority is None:
            raise ProviderError('MODEL_SEND_AUTHORIZATION_REQUIRED')
        bound = authority.reserve_bound(call)
        month_authority = authority.monthly(call)
        grant = await g.permissions.resolve(call)
        origins = g._permission(call, grant)
        if bound.input_upper > grant.max_input_tokens or bound.output_upper > grant.max_output_tokens:
            raise ProviderError('MODEL_INPUT_HARD_LIMIT_DENIED')
        # A savepoint is not a committed admission. LeasedSendGate owns a fresh
        # autocommit connection; arbitrary callers with outer transactions deny.
        from psycopg.pq import TransactionStatus
        if not self.db.autocommit or self.db.info.transaction_status != TransactionStatus.IDLE:
            raise ProviderError('MODEL_ADMISSION_OWN_TRANSACTION_REQUIRED')
        total = bound.input_upper + bound.output_upper
        async with self.db.transaction():
            await self.db.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            await g._call_lock(call)
            await g._deny_existing(call)
            clock = await g._one("SELECT (date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC'))::date AS month")
            month = clock['month']
            if month != month_authority.month:
                raise ProviderError('TOKEN_MONTH_CHANGED')
            key = (month_authority.account_id, month_authority.provider, month)
            await self.db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('weknora-token-month:' + encode([str(k) for k in key]),))
            # READ COMMITTED gives each statement a post-lock current snapshot.
            current = await g._one("SELECT (date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC'))::date AS month")
            if current['month'] != month:
                raise ProviderError('TOKEN_MONTH_CHANGED')
            grant = await g.permissions.resolve(call)
            origins = g._permission(call, grant)
            if call.context.cancelled and call.context.cancelled.is_set():
                raise ProviderError('MODEL_CALL_CANCELLED')
            import asyncio
            if call.context.deadline is not None and call.context.deadline <= asyncio.get_running_loop().time():
                raise ProviderError('MODEL_ADMISSION_TIMEOUT')
            await self.db.execute('INSERT INTO port_token_budget_months(account_id,provider,month,limit_tokens,carry_in_tokens,carry_status,carry_source,approval_source) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (*key, month_authority.limit_tokens, month_authority.carry_in_tokens,
                 month_authority.carry_status, month_authority.carry_source, month_authority.approval_source))
            stored = await g._one('SELECT * FROM port_token_budget_months WHERE account_id=%s AND provider=%s AND month=%s FOR UPDATE', key)
            if (stored['limit_tokens'] != month_authority.limit_tokens
                    or stored['carry_in_tokens'] != month_authority.carry_in_tokens
                    or stored['carry_status'] != month_authority.carry_status
                    or stored['carry_source'] != month_authority.carry_source
                    or not same_month_authority(stored['approval_source'], month_authority.approval_source)):
                raise ProviderError('TOKEN_MONTH_AUTHORITY_CONFLICT')
            if stored['blocked_reason']:
                raise ProviderError('TOKEN_MONTH_BLOCKED')
            used = await g._one("SELECT COALESCE(SUM(CASE WHEN state IN ('reserved','unknown') THEN held_tokens WHEN state='settled' THEN actual_total ELSE 0 END),0) AS used FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month=%s", key)
            if month_authority.carry_in_tokens + used['used'] + total > month_authority.limit_tokens:
                raise ProviderError('MONTHLY_TOKEN_LIMIT_EXCEEDED')
            identity = g._slot_id(call)
            await self.db.execute('INSERT INTO port_call_slots(call_id,unit,tenant_id,subject_id,fingerprint) VALUES(%s,%s,%s,%s,%s)',
                (identity, 'tokens', g.tenant_id, call.context.scope.owner_id, fingerprint(call)))
            await self.db.execute('INSERT INTO port_token_calls(call_id,request_id,tenant_id,subject_id,session_id,account_id,provider,budget_provider,month,fingerprint,wire_sha256,wire_bytes,model,model_id,capability,endpoint,retrieval_kbs,origin_kbs,reserved_input,reserved_output,held_tokens,bound_source,permission_source) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)',
                (identity, call.context.request_id, g.tenant_id, call.context.scope.owner_id, call.context.session_id,
                 month_authority.account_id, call.provider, month_authority.provider, month, fingerprint(call),
                 call.wire_sha256, call.wire_bytes, call.model, call.model_id, call.capability, call.endpoint,
                 encode(call.context.scope.knowledge_base_ids), encode(origins), bound.input_upper, bound.output_upper,
                 total, bound.source, grant.approval_source))
        # Return only after the durable transaction commit, never before.

    async def finish(self, call, *, outcome, usage):
        g = self.gate
        g.guard.require_verified()
        if outcome not in ('NOT_SENT','UNKNOWN','RESPONSE_RECEIVED') or not isinstance(usage,TokenUsage):
            raise ProviderError('MODEL_FINISH_EVIDENCE_INVALID', sent=True)
        facts = usage.provider_usage_facts
        numeric = json.loads(encode(facts.public())) if isinstance(facts, ProviderUsageFacts) else None
        actual, lower = facts.accounting() if isinstance(facts, ProviderUsageFacts) else (None, 0)
        overrun = False
        async with self.db.transaction():
            await g._call_lock(call)
            row = await g._one('SELECT * FROM port_token_calls WHERE call_id=%s AND tenant_id=%s AND subject_id=%s',
                (g._slot_id(call), g.tenant_id, call.context.scope.owner_id))
            if row is None or row['fingerprint'] != fingerprint(call):
                raise ProviderError('MODEL_FINISH_IDENTITY_MISMATCH', sent=True)
            key = (row['account_id'], row['budget_provider'], row['month'])
            await self.db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('weknora-token-month:' + encode([str(k) for k in key]),))
            row = await g._one('SELECT * FROM port_token_calls WHERE call_id=%s FOR UPDATE', (row['call_id'],))
            if row['state'] in ('settled','released'):
                if row['outcome'] != outcome or row['usage_facts'] != numeric:
                    raise ProviderError('MODEL_FINISH_TERMINAL_CONFLICT', sent=True)
                return
            prior=row['usage_facts']
            if numeric is None and prior is not None:
                numeric=prior
            elif prior is not None and numeric!=prior:
                history=list(prior.get('prior_finish_numeric_raw',[]))
                if prior['numeric_raw'] not in history:history.append(prior['numeric_raw'])
                numeric={**numeric,'prior_finish_numeric_raw':history}
            reserved = row['reserved_input'] + row['reserved_output']
            lower = max(lower, row['known_lower_bound'])
            held = max(reserved, lower)
            state, settled = 'unknown', None
            if outcome == 'NOT_SENT':
                if row['state'] != 'reserved' or lower:
                    raise ProviderError('MODEL_UNKNOWN_CANNOT_RELEASE', sent=True)
                state, held = 'released', 0
            elif actual is not None and outcome == 'RESPONSE_RECEIVED' and lower <= reserved:
                state, settled, held = 'settled', actual, actual
            component_overrun=(isinstance(facts,ProviderUsageFacts) and (
                (facts.get('input').value or 0)>row['reserved_input'] or
                (facts.get('output').value or 0)>row['reserved_output']))
            if lower > reserved or component_overrun:
                overrun = True
                state,settled,held='unknown',None,max(reserved,lower)
                await self.db.execute("UPDATE port_token_budget_months SET blocked_reason='USAGE_EXCEEDS_RESERVATION' WHERE account_id=%s AND provider=%s AND month=%s", key)
            await self.db.execute('UPDATE port_token_calls SET state=%s,outcome=%s,known_lower_bound=%s,held_tokens=%s,actual_total=%s,usage_facts=%s::jsonb,finished_at=now() WHERE call_id=%s',
                (state, outcome, lower, held, settled, encode(numeric), row['call_id']))
        if overrun:
            raise ProviderError('TOKEN_USAGE_EXCEEDS_RESERVATION', sent=True)


async def ledger_carry_in(connection, account_id, provider, month):
    """Conservative carry-in for a new UTC month, read from the existing token ledger.

    Sums strictly earlier months of the SAME account/provider: settled rows use
    their actual total, reserved/unknown rows use their held (already a conservative
    occupancy), so the result is never below the real prior consumption. Rows with
    unknown usage make the bound UNKNOWN (still a positive conservative value), never 0.
    Read-only; no write, no reset, no money conversion.
    """
    if not account_id or not provider:
        raise ProviderError('TOKEN_MONTH_AUTHORITY_REQUIRED')
    row = await (await connection.execute(
        "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual_total "
        "WHEN state IN ('reserved','unknown') THEN held_tokens ELSE 0 END),0) AS carried, "
        "COUNT(*) FILTER (WHERE state='unknown') AS unknown_rows "
        "FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month < %s",
        (account_id, provider, month))).fetchone()
    return {'carry_in_tokens': int(row['carried']),
            'carry_status': 'UNKNOWN' if row['unknown_rows'] else 'KNOWN',
            'carry_source': 'LEDGER:port_token_calls:<' + str(month)}
