"""Actual HTTP state machine over in-memory sockets; no network, DB or keys."""
import http.client
import io
import json
import platform
from unittest.mock import patch

from backend.app.adapters.models import cloud
from backend.app.ports.providers import ProviderRequestNotSent


class MemorySocket:
    def __init__(self, writes):
        self.writes, self.closed = writes, False

    def settimeout(self, timeout):
        assert 0 < timeout <= 10

    def sendall(self, data):
        self.writes.append(bytes(data))

    def makefile(self, *args, **kwargs):
        return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')

    def close(self):
        self.closed = True


def run_case(case):
    import ssl
    attempts, fences, writes, sockets = [], [], [], []

    def connect(connection):
        attempts.append(1)
        connection.sock = MemorySocket(writes)
        sockets.append(connection.sock)
        if case == 'certificate':
            raise ssl.SSLCertVerificationError(1, 'SIMULATED')
        if len(attempts) == 1 or case == 'terminal_eof':
            raise ssl.SSLEOFError(8, 'SIMULATED_PRE_HTTP_EOF')

    def fence():
        fences.append(1)
        if case == 'revocation' and len(fences) == 2:
            raise ProviderRequestNotSent('MODEL_PRETRANSPORT_FENCE_DENIED')

    connection = cloud._ObservedHTTPSConnection('example.invalid', timeout=10,
                                                before_send=fence)
    failure = None
    try:
        with patch.object(http.client.HTTPSConnection, 'connect', new=connect):
            try:
                connection.request('POST', '/chat/completions', body=b'{}',
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                assert case == 'success_after_eof'
                assert response.status == 200 and response.read() == b'{}'
            except Exception as error:
                failure = error
        http_requests = sum(data.startswith(b'POST ') for data in writes)
        if case == 'success_after_eof':
            assert failure is None
            assert len(attempts) == 2 and len(fences) == 3 and http_requests == 1
            assert sockets[0].closed and b''.join(writes).endswith(b'\r\n\r\n{}')
        else:
            assert isinstance(failure, ProviderRequestNotSent)
            assert not writes and http_requests == 0 and all(s.closed for s in sockets)
            assert len(attempts) == (2 if case == 'terminal_eof' else 1)
            assert len(fences) == (1 if case == 'certificate' else 2)
            if case != 'revocation':
                assert type(failure) is cloud._ConnectionNotSent
        return dict(case=case, status='PASS', connection_attempts=len(attempts),
                    guard_checks=len(fences), http_requests=http_requests,
                    http_write_calls=len(writes))
    except Exception:
        # Only fixed types/counts leave this synthetic harness; no error text.
        return dict(case=case, status='FAIL', connection_attempts=len(attempts),
                    guard_checks=len(fences),
                    http_requests=sum(data.startswith(b'POST ') for data in writes),
                    error_type=type(failure).__name__ if failure is not None else 'AssertionError')
    finally:
        connection.close()


def main():
    cases = [run_case(case) for case in ('success_after_eof', 'terminal_eof',
                                       'certificate', 'revocation')]
    status = 'PASS' if all(case['status'] == 'PASS' for case in cases) else 'FAIL'
    print(json.dumps(dict(status=status, python_version=platform.python_version(),
                         check_kind='OFFLINE_ACTUAL_HTTP_STATE_MACHINE_SIMULATED_SOCKETS',
                         provider_calls=0, network_calls=0, database_calls=0, cases=cases)))
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
