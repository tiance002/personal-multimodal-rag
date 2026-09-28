import importlib
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.ports.providers import ProviderUnavailable


def subject():
    assert importlib.util.find_spec('backend.app.adapters.models.usage') is not None, 'local model usage capture is missing'
    return importlib.import_module('backend.app.adapters.models.usage')


@pytest.fixture
def model_server():
    responses = []
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            status, payload = responses.pop(0)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield OllamaGateway(f'http://127.0.0.1:{server.server_port}', 'qwen-test', 'bge-test'), responses, requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_actual_usage_is_captured_without_changing_answer_contract(model_server):
    usage = subject()
    gateway, responses, requests = model_server
    responses.extend([(200, {'message': {'content': '{"expansions":["fact"]}'}, 'prompt_eval_count': 71, 'eval_count': 6}),
                      (200, {'message': {'content': 'Fact [E1].'}, 'prompt_eval_count': 123, 'eval_count': 11})])
    prompt = 'FULL SYSTEM INSTRUCTION\nHISTORY\nEVIDENCE\nquestion'
    with usage.capture_usage() as captured:
        gateway.query_expand('question', 5)
        answer = gateway.answer(prompt, 5)
    assert answer == 'Fact [E1].'
    assert requests[1]['messages'][0]['content'] == prompt
    assert [call.stage for call in captured.calls] == ['query', 'answer']
    report = captured.summary()
    assert report['query']['input_tokens'] == 71
    assert report['answer']['input_tokens'] == 123
    assert report['business_total_tokens'] == 211
    assert all(call.latency_ms >= 0 for call in captured.calls)
    assert prompt not in json.dumps(captured.to_dict())


def test_missing_and_failed_retry_usage_is_unavailable_not_zero(model_server):
    usage = subject()
    gateway, responses, _ = model_server
    responses.extend([(503, {'error':'unavailable'}),
                      (200, {'message': {'content': 'retry answer'}, 'prompt_eval_count': 20, 'eval_count': 5})])
    with usage.capture_usage() as captured:
        with pytest.raises(ProviderUnavailable):
            gateway.answer('question', 5)
        gateway.answer('question', 5)
    assert len(captured.calls) == 2
    assert captured.calls[0].status == 'error'
    assert captured.calls[0].input_tokens is None
    assert captured.summary()['answer']['input_tokens'] is None
    assert captured.summary()['answer']['known_input_tokens'] == 20
    assert captured.summary()['business_total_tokens'] is None


def test_missing_success_usage_and_judge_budget_remain_separate(model_server):
    usage = subject()
    gateway, responses, _ = model_server
    responses.extend([(200, {'message': {'content': 'business answer'}}),
                      (200, {'message': {'content': 'judge verdict'}, 'prompt_eval_count': 9, 'eval_count': 3})])
    with usage.capture_usage() as business:
        gateway.answer('business prompt', 5)
        with usage.capture_usage(role='judge') as judge:
            gateway.answer('judge prompt', 5)
    assert len(business.calls) == 1
    assert business.summary()['business_total_tokens'] is None
    assert judge.summary()['judge_total_tokens'] == 12
    assert judge.summary()['business_total_tokens'] is None
