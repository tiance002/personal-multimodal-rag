from __future__ import annotations

import ipaddress
import argparse
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from eval_center.store import (
    compare_experiments,
    get_experiment,
    initialize_database,
    list_experiments,
)


_MAX_REQUEST_TARGET = 2048
_MAX_PAGE_BYTES = 1_000_000
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


def _resolve_loopback(host: str, port: int) -> tuple[str, int]:
    if not isinstance(host, str) or not host or len(host) > 253:
        raise ValueError("host must resolve to loopback")
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("host must resolve to loopback") from exc
    if not results:
        raise ValueError("host must resolve to loopback")
    resolved: list[tuple[int, str]] = []
    for family, _socktype, _proto, _canonname, sockaddr in results:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("host must resolve to loopback") from exc
        if not ip.is_loopback:
            raise ValueError("host must resolve to loopback")
        resolved.append((family, address))
    family, address = resolved[0]
    if family not in (socket.AF_INET, socket.AF_INET6):
        raise ValueError("host must resolve to loopback")
    return address, family


def make_server(database_path: Path, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    bind_host, address_family = _resolve_loopback(host, port)
    database_path = Path(database_path)
    initialize_database(database_path)
    page_path = Path(__file__).resolve().parent / "static" / "index.html"

    class ReadOnlyEvaluationHandler(BaseHTTPRequestHandler):
        server_version = "EvalCenter/2"
        sys_version = ""

        def log_message(self, _format: str, *args: object) -> None:
            # Request targets can contain user supplied values; do not write them to logs.
            return

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self,
            status: int,
            payload: object,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body, extra_headers)

        def _error(self, status: int, code: str) -> None:
            self._send_json(status, {"error": code})

        def do_GET(self) -> None:
            if len(self.path) > _MAX_REQUEST_TARGET:
                self._error(400, "invalid_request")
                return
            try:
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.fragment:
                    self._error(400, "invalid_request")
                    return
                query_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
                query: dict[str, str] = {}
                for key, value in query_pairs:
                    if key in query:
                        self._error(400, "invalid_request")
                        return
                    query[key] = value
            except (ValueError, UnicodeError):
                self._error(400, "invalid_request")
                return

            if parsed.path == "/healthz":
                self._send_json(200, {"status": "ok", "git_sha": os.environ.get("EVAL_CENTER_CODE_SHA")})
                return
            if parsed.path == "/":
                if query:
                    self._error(400, "invalid_request")
                    return
                try:
                    body = page_path.read_bytes()
                except OSError:
                    self._error(500, "internal_error")
                    return
                if len(body) > _MAX_PAGE_BYTES:
                    self._error(500, "internal_error")
                    return
                self._send(200, "text/html; charset=utf-8", body)
                return
            if parsed.path == "/api/v1/experiments":
                if set(query) - {"limit", "offset", "view"} or query.get('view','official') not in ('official','diagnostic'):
                    self._error(400, "invalid_request")
                    return
                try:
                    limit = int(query.get("limit", "50"))
                    offset = int(query.get("offset", "0"))
                    experiments = list_experiments(database_path, limit=limit, offset=offset,
                                                   include_unverified=query.get('view') == 'diagnostic')
                except (ValueError, OverflowError):
                    self._error(400, "invalid_request")
                    return
                except Exception:
                    self._error(500, "internal_error")
                    return
                self._send_json(200, {"experiments": experiments})
                return
            detail_prefix = "/api/v1/experiments/"
            if parsed.path.startswith(detail_prefix):
                if query:
                    self._error(400, "invalid_request")
                    return
                experiment_id = parsed.path[len(detail_prefix):]
                if not experiment_id or "/" in experiment_id:
                    self._error(404, "not_found")
                    return
                try:
                    experiment = get_experiment(database_path, experiment_id)
                except Exception:
                    self._error(500, "internal_error")
                    return
                if experiment is None:
                    self._error(404, "not_found")
                    return
                self._send_json(200, experiment)
                return
            if parsed.path == "/api/v1/compare":
                if set(query) != {"ids"}:
                    self._error(400, "invalid_request")
                    return
                experiment_ids = query["ids"].split(",")
                try:
                    comparison = compare_experiments(database_path, experiment_ids)
                except ValueError:
                    self._error(400, "invalid_request")
                    return
                except Exception:
                    self._error(500, "internal_error")
                    return
                self._send_json(200, comparison)
                return
            self._error(404, "not_found")

        def _reject_write(self) -> None:
            self._send_json(405, {"error": "method_not_allowed"}, {"Allow": "GET"})

        def do_POST(self) -> None:
            self._reject_write()

        def do_PUT(self) -> None:
            self._reject_write()

        def do_PATCH(self) -> None:
            self._reject_write()

        def do_DELETE(self) -> None:
            self._reject_write()

    class LoopbackServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    LoopbackServer.address_family = address_family

    return LoopbackServer((bind_host, port), ReadOnlyEvaluationHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the private loopback-only evaluation dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    database_path = Path(os.environ.get("EVAL_CENTER_DB", "var/eval-center/registry.sqlite3"))
    try:
        server = make_server(database_path, host=args.host, port=args.port)
    except (OSError, ValueError):
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
