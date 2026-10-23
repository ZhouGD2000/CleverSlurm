import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cslurm.ai.models import ModelDiscoveryError, endpoints, list_models


class FakeOpener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())


def http_error(code):
    return urllib.error.HTTPError('https://example.invalid/secret-url', code, 'private-error', {}, io.BytesIO(b'private-key'))


def discover(opener, **kw):
    return list_models(base_url='https://example.invalid/v1', api_key='private-key', opener=opener, **kw)


@pytest.mark.parametrize('base,format,expected', [
    ('https://a.invalid/v1/', 'openai', ['https://a.invalid/v1/models']),
    ('https://a.invalid', 'openai', ['https://a.invalid/models', 'https://a.invalid/v1/models']),
    ('https://a.invalid/v4', 'openai', ['https://a.invalid/v4/models']),
    ('https://a.invalid/v1/chat/completions', 'openai', ['https://a.invalid/v1/models']),
    ('https://a.invalid/v1/responses', 'openai', ['https://a.invalid/v1/models']),
    ('https://a.invalid', 'anthropic', ['https://a.invalid/v1/models', 'https://a.invalid/models']),
    ('https://a.invalid/v1/messages', 'anthropic', ['https://a.invalid/v1/models']),
    ('https://a.invalid/v1/models', 'openai', ['https://a.invalid/v1/models']),
])
def test_model_endpoint_paths(base, format, expected):
    assert [url for url, _ in endpoints(base, format)] == expected


def test_compatible_anthropic_path_uses_same_origin_fallback():
    assert endpoints('https://a.invalid/anthropic', 'anthropic') == [
        ('https://a.invalid/anthropic/v1/models', 'anthropic'),
        ('https://a.invalid/anthropic/models', 'anthropic'),
        ('https://a.invalid/models', 'openai'), ('https://a.invalid/v1/models', 'openai')]


def test_protocol_headers_and_sorted_models():
    opener = FakeOpener({'data': [{'id': 'z'}, {'id': 'a', 'display_name': 'Model A'}, {'id': 'z'}, None, {'id': 1}]})
    result = discover(opener)
    assert result['models'] == [{'id': 'a', 'name': 'Model A'}, {'id': 'z', 'name': ''}]
    request, timeout = opener.requests[0]
    assert request.get_method() == 'GET' and request.data is None
    assert request.get_header('Authorization') == 'Bearer private-key'
    assert request.get_header('X-api-key') is None and timeout <= 5
    opener = FakeOpener({'data': []})
    assert discover(opener, format='anthropic')['models'] == []
    request = opener.requests[0][0]
    assert request.get_header('X-api-key') == 'private-key'
    assert request.get_header('Anthropic-version') == '2023-06-01'
    assert request.get_header('Authorization') is None


def test_fallback_only_for_missing_endpoint():
    opener = FakeOpener(http_error(404), http_error(405), {'data': [{'id': 'model'}]})
    result = list_models(base_url='https://a.invalid/anthropic', format='anthropic', api_key='private-key', opener=opener)
    assert result['models'][0]['id'] == 'model'
    assert opener.requests[-1][0].get_header('Authorization') == 'Bearer private-key'
    assert opener.requests[-1][0].get_header('X-api-key') is None


@pytest.mark.parametrize('status,code', [(401, 'unauthorized'), (403, 'forbidden'), (429, 'rate_limited'), (500, 'http_error'), (302, 'redirect')])
def test_http_errors_are_distinct_and_private(status, code):
    opener = FakeOpener(http_error(status))
    with pytest.raises(ModelDiscoveryError) as error:
        discover(opener)
    assert error.value.code == code
    assert 'private' not in str(error.value) and 'secret-url' not in str(error.value)
    assert len(opener.requests) == 1


def test_unsupported_and_invalid_or_large_responses():
    for response, code in [(http_error(404), 'unsupported'), (b'<html>bad</html>', 'invalid_response'),
                           ({'message': 'not a catalog'}, 'invalid_response'),
                           ({'data': [{'id': 'invalid id'}]}, 'invalid_response'),
                           (b'x' * (1024 * 1024 + 1), 'too_large'), (TimeoutError(), 'connection')]:
        with pytest.raises(ModelDiscoveryError) as error:
            discover(FakeOpener(response))
        assert error.value.code == code


def test_pagination_uses_cursor_not_server_supplied_urls():
    opener = FakeOpener({'data': [{'id': 'b'}], 'has_more': True, 'last_id': 'cursor/one', 'next': 'https://evil.invalid'},
                        {'data': [{'id': 'a'}, {'id': 'b'}], 'has_more': False})
    result = discover(opener, format='anthropic')
    assert len(result['models']) == 2 and not result['truncated']
    assert opener.requests[1][0].full_url == 'https://example.invalid/v1/models?after_id=cursor%2Fone'


def test_repeated_pagination_and_model_limit_are_bounded():
    page = {'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'}
    opener = FakeOpener(page, page)
    assert discover(opener)['truncated']
    assert len(opener.requests) == 2
    result = discover(FakeOpener({'data': [{'id': f'model-{i}'} for i in range(2100)]}))
    assert len(result['models']) == 2000 and result['truncated']


def test_alternative_shapes_and_secret_echo_are_sanitized():
    result = discover(FakeOpener({'models': [{'slug': 'slug-model'}, {'id': 'id-model'}, 'string-model',
                                                   {'id': 'private-key'}, {'id': 'ok', 'name': 'private-key'}]}))
    assert len(result['models']) == 4
    assert 'private-key' not in json.dumps(result)


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://key:secret@a.invalid', 'http://a.invalid', 'https://a.invalid?key=secret'])
def test_invalid_endpoint_rejected_before_request(url):
    with pytest.raises(ValueError):
        endpoints(url, 'openai')


def test_explicit_catalog_stays_on_same_origin():
    assert endpoints('https://a.invalid/chat', 'openai', 'https://a.invalid/catalog') == [('https://a.invalid/catalog', 'openai')]
    with pytest.raises(ValueError):
        endpoints('https://a.invalid', 'openai', 'https://evil.invalid/models')


def test_real_http_redirect_never_forwards_credentials():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/credential-collector')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with pytest.raises(ModelDiscoveryError) as error:
            list_models(base_url=f'http://127.0.0.1:{server.server_port}', api_key='private-key')
        assert error.value.code == 'redirect'
        assert seen == ['/models']
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
