"""Self-contained SSH stdio bootstrap for existing installed cweb APIs."""

import http.client
import importlib.util
import importlib.metadata
import json
import os
import sys
import threading
import time


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    if sys.version_info < (3, 10):
        emit({'status': 'incompatible', 'error': 'Python 3.10 or newer is required'})
        return
    options = json.loads(sys.argv[1])
    if importlib.util.find_spec('cslurm') is None:
        emit({'status': 'not_installed', 'python': sys.executable})
        return
    os.environ['CSLURM_ROOT'] = os.path.expanduser(options['root'])
    try:
        import cslurm
        from cslurm.web.server import ConsoleServer
        server = ConsoleServer(0, read_only=options['read_only'], allow_cancel=options['allow_cancel'])
    except Exception:
        emit({'status': 'incompatible', 'error': 'Installed CleverSlurm cannot start its web API; check its environment and version'})
        return
    # Connection never initializes SQLite, starts cron/workers or sends messages.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        version = importlib.metadata.version('CleverSlurm')
    except importlib.metadata.PackageNotFoundError:
        version = 'source'
    emit({'status': 'connected', 'protocol': 1, 'python': sys.executable,
          'package': cslurm.__file__, 'version': version, 'root': os.environ['CSLURM_ROOT']})
    try:
        while True:
            line = sys.stdin.buffer.readline(65537)
            if not line or len(line) > 65536:
                break
            message = json.loads(line)
            body = message.get('body')
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=65)
            try:
                headers = {'Authorization': 'Bearer ' + server.token}
                payload = None
                if body is not None:
                    headers['Content-Type'] = 'application/json'
                    payload = json.dumps(body).encode()
                connection.request('POST' if body is not None else 'GET', message['path'], payload, headers)
                response = connection.getresponse()
                content = response.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    emit({'status': 502, 'data': {'error': 'Remote response exceeds size limit'}})
                else:
                    emit({'status': response.status, 'data': json.loads(content)})
            except Exception:
                emit({'status': 502, 'data': {'error': 'Remote API request failed; no automatic retry'}})
            finally:
                connection.close()
    finally:
        # EOF should let already accepted background work finish.
        deadline = time.monotonic() + 310
        while server.operations.active and time.monotonic() < deadline:
            time.sleep(.2)
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
