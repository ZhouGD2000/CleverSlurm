"""Subprocess isolation provides a hard deadline for DNS/TLS/HTTP diagnostics."""
import json
import sys

from cslurm.web import settings


def main():
    body = json.load(sys.stdin)
    try:
        result = settings.fetch_models(body) if sys.argv[1] == 'models' else settings.test_connection(body)
    except Exception as exc:
        code = getattr(exc, 'code', None)
        messages = {
            'authentication': '密钥无效或已过期（HTTP 401）。',
            'permission': '账户权限或订阅不允许调用（HTTP 403）。',
            'rate_limit': '服务商限流（HTTP 429）。',
            'connection': '连接失败，请检查服务器 DNS、网络与 TLS。',
            'dns': '服务器无法解析模型服务的域名。',
            'tls': 'TLS 握手或证书校验失败。',
            'connect_timeout': '服务器连接模型服务超时。',
            'timeout': '模型响应超时。',
        }
        if isinstance(exc, settings.SettingsConflict):
            result = {'error': '配置已改变，请重新载入后测试。', 'code': 'conflict'}
        elif isinstance(exc, ValueError):
            result = {'error': '配置无效，请检查字段及附加 JSON。', 'code': 'configuration'}
        else:
            from cslurm.ai.models import ModelDiscoveryError
            result = {'error': str(exc) if isinstance(exc, ModelDiscoveryError) else messages.get(code, '请求失败，请检查配置或服务商状态。'),
                      'code': code or 'request_error'}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
