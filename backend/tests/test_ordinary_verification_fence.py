"""SIMULATED grant/calls; real Linux filesystem and process locks, no providers/DB."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.ordinary_verification_fence import TaskFence, TOKEN_LIMIT, KB, DOC, POLICIES, SESSION, install_fence, allowed_mutation
from backend.app.domain.scope import Scope


def competing_admission(grant, sha, journal, batch, run, request_id, queue):
    try:
        TaskFence(Path(grant), sha, Path(journal), batch_root=Path(batch)).admit(
            run_id=run, request_id=request_id, role='chat_cheap', policy_id=POLICIES['chat_cheap'],
            scope=Scope.from_ids([KB], [DOC]), input_tokens=100, output_tokens=TOKEN_LIMIT//2)
        queue.put('ADMITTED')
    except ValueError as error:
        queue.put(str(error))


@unittest.skipUnless(os.name == 'posix', 'Actual task fence executes on Linux; requires native flock/directory fsync')
class FenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.probe = self.root / 'probe-fixture.json'
        self.probe.write_text(json.dumps(dict(state='UNKNOWN', run_id='old-unknown',
                                             input_reserved=39, output_reserved=1024)))
        self.runs = [str(uuid.uuid4()) for _ in range(7)]
        self.grant = self.root / 'grant.json'
        self.value = dict(schema_version=4, owner_authorization='OFFLINE_SIMULATED_OWNER_DECISION',
                          run_ids=self.runs, probe_snapshot={self.probe.name: hashlib.sha256(self.probe.read_bytes()).hexdigest()},
                          acknowledged_unknown_runs=['old-unknown'], acknowledged_unknown_web_requests=[])
        self.journal = self.root / 'journal.json'
        self.write_grant()
        self.fence = TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)

    def write_grant(self):
        self.grant.write_text(json.dumps(self.value))
        self.sha = hashlib.sha256(self.grant.read_bytes()).hexdigest()
        self.journal.write_text(json.dumps(dict(grant_sha256=self.sha, entries=[])))

    def admit(self, **changes):
        kwargs = dict(run_id=self.runs[0], request_id='fixture.embedding.1', role='embedding',
                      policy_id=POLICIES['embedding'], scope=Scope.from_ids([KB], [DOC]),
                      input_tokens=100, output_tokens=0)
        kwargs.update(changes)
        self.fence.admit(**kwargs)

    def test_restart_keeps_full_bound_and_duplicate_denial(self):
        self.admit()
        restarted = TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)
        self.assertEqual(restarted.seed, 1063)
        self.assertEqual(restarted.read_journal()['entries'][0]['tokens'], 100)
        self.fence = restarted
        with self.assertRaisesRegex(ValueError, 'ALREADY_ADMITTED'):
            self.admit()

    def test_exact_aggregate_limit_and_one_token_over(self):
        self.admit(input_tokens=TOKEN_LIMIT-1063)
        with self.assertRaisesRegex(ValueError, 'OWNER_TASK_TOKEN_LIMIT'):
            self.admit(request_id='fixture.second', input_tokens=1)

    def test_scope_role_run_and_bound_denials(self):
        for kwargs in ({'scope': Scope.from_ids(['other'], [DOC])},
                       {'scope': Scope.from_ids([KB])}, {'role': 'vision'},
                       {'run_id': 'old-unknown'}, {'policy_id': 'rotated-policy'},
                       {'input_tokens': True}, {'output_tokens': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'TASK_REQUEST_DENIED'):
                self.admit(**kwargs)
        self.assertEqual(self.fence.read_journal()['entries'], [])

    def test_changed_probe_or_new_probe_cannot_reset_seed(self):
        self.probe.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'PROBE_BATCH_CHANGED'):
            self.admit()

    def test_missing_or_corrupt_journal_fails_closed(self):
        self.journal.unlink()
        with self.assertRaises(FileNotFoundError):
            TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)
        self.journal.write_text(json.dumps(dict(grant_sha256='wrong', entries=[])))
        with self.assertRaisesRegex(ValueError, 'TASK_JOURNAL_INVALID'):
            TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)

    def test_fresh_unknown_requires_new_owner_acknowledgment(self):
        self.value['acknowledged_unknown_runs'] = []
        self.write_grant()
        with self.assertRaisesRegex(ValueError, 'FRESH_OWNER_DECISION_REQUIRED'):
            TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)

    def test_grant_bytes_must_match_pinned_hash(self):
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
            TaskFence(self.grant, '0'*64, self.journal, batch_root=self.root)

    def test_v1_authority_cannot_enable_v2_limits(self):
        self.value['schema_version'] = 1
        self.write_grant()
        with self.assertRaisesRegex(ValueError, 'OWNER_GRANT_INVALID'):
            TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)

    def test_carried_admissions_still_consume_full_bounds_after_new_grant(self):
        self.admit(input_tokens=12000, output_tokens=2048)
        entries = self.fence.read_journal()['entries']
        self.value['owner_authorization'] = 'OFFLINE_SIMULATED_EXPLICIT_EXTENSION'
        self.write_grant()
        self.journal.write_text(json.dumps(dict(grant_sha256=self.sha, entries=entries)))
        fence = TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)
        self.assertEqual(fence.read_journal()['entries'], entries)
        with self.assertRaisesRegex(ValueError, 'OWNER_TASK_TOKEN_LIMIT'):
            fence.admit(run_id=self.runs[4], request_id='fixture.extension', role='embedding',
                policy_id=POLICIES['embedding'], scope=Scope.from_ids([KB], [DOC]),
                input_tokens=TOKEN_LIMIT-1063-14048+1, output_tokens=0)

    def test_explicit_ack_keeps_old_unknown_bound_and_stops_next_unknown(self):
        from scripts.ordinary_verification_fence import check_unknown_stop
        from sqlalchemy import create_engine, text
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        self.admit(request_id='fixture.old-web-unknown', input_tokens=17526, output_tokens=4096)
        entries = self.fence.read_journal()['entries']
        self.value['acknowledged_unknown_web_requests'] = ['fixture.old-web-unknown']
        self.write_grant()
        self.journal.write_text(json.dumps(dict(grant_sha256=self.sha, entries=entries)))
        fence = TaskFence(self.grant, self.sha, self.journal, batch_root=self.root)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE service_token_reservations(request_id TEXT,policy_id TEXT,state TEXT)'))
            connection.execute(text("INSERT INTO service_token_reservations VALUES (:id,:policy,'unknown')"),
                               {'id':'fixture.old-web-unknown','policy':POLICIES['embedding']})
        container = SimpleNamespace(engine=engine)
        check_unknown_stop(container,fence)
        self.assertEqual(sum(r['tokens'] for r in fence.read_journal()['entries']),21622)
        fence.admit(run_id=self.runs[-1], request_id='fixture.next-web-unknown', role='embedding',
            policy_id=POLICIES['embedding'],scope=Scope.from_ids([KB],[DOC]),input_tokens=42,output_tokens=0)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO service_token_reservations VALUES (:id,:policy,'unknown')"),
                               {'id':'fixture.next-web-unknown','policy':POLICIES['embedding']})
        with self.assertRaisesRegex(ValueError,'UNKNOWN_STOP_REQUIRED'):
            check_unknown_stop(container,fence)

    def test_ack_cannot_name_an_unadmitted_request(self):
        self.value['acknowledged_unknown_web_requests'] = ['not-an-admission']
        self.write_grant()
        with self.assertRaisesRegex(ValueError,'UNKNOWN_ACK_NOT_IN_CARRIED_JOURNAL'):
            TaskFence(self.grant,self.sha,self.journal,batch_root=self.root)

    def test_native_cross_process_serialization(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        children = [context.Process(target=competing_admission, args=(str(self.grant), self.sha,
            str(self.journal), str(self.root), self.runs[0], f'fixture.parallel.{n}', queue)) for n in range(2)]
        for child in children:
            child.start()
        for child in children:
            child.join(10)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(sorted(queue.get(timeout=2) for _ in children), ['ADMITTED', 'OWNER_TASK_TOKEN_LIMIT'])
        self.assertEqual(len(self.fence.read_journal()['entries']), 1)
        queue.close()

    def test_wrapper_preserves_existing_reservation_and_blocks_before_delegate(self):
        from backend.app.ports.run_lifecycle import current_execution, execution_owner
        from backend.app.ports.providers import ProviderRequestNotSent
        original = Mock(return_value='ORIGINAL_LEDGER_RESERVATION')
        from sqlalchemy import create_engine, text
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE service_token_reservations(request_id TEXT,policy_id TEXT,state TEXT)'))
        original_check = Mock(return_value=None)
        guard = SimpleNamespace(reserve_prepared=original, check_before_send=original_check, spec=SimpleNamespace(role='embedding'),
                                policy=SimpleNamespace(policy_id=POLICIES['embedding']))
        container = SimpleNamespace(engine=engine, provider_factory=SimpleNamespace(service_bindings={
            'embedding': SimpleNamespace(usage_guard=guard)}))
        install_fence(container, self.fence)
        kwargs = dict(request_id='fixture.guarded', scope=Scope.from_ids([KB], [DOC]),
                      wire_sha256='f'*64, input_tokens=10, output_tokens=0)
        claim = SimpleNamespace(run_id=self.runs[0], owner='fixture-owner', created=True, state='IN_PROGRESS')
        repository = SimpleNamespace(read_run=Mock(return_value={'id': claim.run_id}))
        token = current_execution.set((claim, SimpleNamespace(repository=repository), SimpleNamespace(is_set=lambda:False), None))
        owner = execution_owner.set((claim.run_id, claim.owner))
        try:
            self.assertEqual(guard.reserve_prepared(**kwargs), 'ORIGINAL_LEDGER_RESERVATION')
            original.assert_called_once_with(**kwargs)
            guard.check_before_send('ORIGINAL_LEDGER_RESERVATION')
            original_check.assert_called_once_with('ORIGINAL_LEDGER_RESERVATION')
            repository.read_run.assert_called_once_with(claim.run_id, SESSION, [KB], [DOC])
            with self.assertRaises(ProviderRequestNotSent):
                guard.reserve_prepared(**kwargs)
            self.assertEqual(original.call_count, 1)
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO service_token_reservations VALUES (:request,:policy,'unknown')"),
                    dict(request=kwargs['request_id'], policy=POLICIES['embedding']))
            with self.assertRaises(ProviderRequestNotSent):
                guard.reserve_prepared(**dict(kwargs, request_id='fixture.next-after-unknown'))
            with self.assertRaises(ProviderRequestNotSent):
                guard.check_before_send('ORIGINAL_LEDGER_RESERVATION')
            self.assertEqual(original.call_count, 1)
            self.assertEqual(original_check.call_count, 1)
            self.assertEqual(len(self.fence.read_journal()['entries']), 1)
        finally:
            current_execution.reset(token)
            execution_owner.reset(owner)
        with self.assertRaises(ProviderRequestNotSent):
            guard.reserve_prepared(**dict(kwargs, request_id='fixture.no-context'))
        self.assertEqual(original.call_count, 1)

    def test_only_exact_original_session_question_and_task_cancel_can_mutate(self):
        self.assertTrue(allowed_mutation('POST', f'/api/v1/conversations/{SESSION}/messages', self.runs))
        self.assertTrue(allowed_mutation('POST', f'/api/v1/runs/{self.runs[0]}/cancel', self.runs))
        for method, path in [('PUT', '/api/v1/settings/models'), ('POST', '/api/v1/conversations'),
                             ('POST', '/api/v1/conversations/other/messages'),
                             ('POST', '/api/v1/runs/old-unknown/cancel')]:
            self.assertFalse(allowed_mutation(method, path, self.runs))


if __name__ == '__main__':
    unittest.main()
