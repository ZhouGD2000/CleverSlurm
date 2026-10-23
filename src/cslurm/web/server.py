import hmac
import json
import mimetypes
import secrets
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cslurm.config import root_dir
from cslurm.web import data, settings
from cslurm.ai.models import ModelDiscoveryError
from cslurm.web.operations import BusyError, Operations
from cslurm.web.remotes import RemoteError, RemoteManager


STATIC = Path(__file__).parent / "static"


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, *, read_only=False, allow_cancel=False, demo=False):
        self.token = secrets.token_urlsafe(32)
        self.database_error = data.check_integrity()
        self.operations = Operations(read_only=read_only, allow_cancel=allow_cancel, demo=demo)
        self.remotes = RemoteManager(root_dir(), read_only=read_only, allow_cancel=allow_cancel, demo=demo)
        super().__init__(("127.0.0.1", port), Handler)

    def server_close(self):
        self.remotes.close()
        self.operations.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        # Access logs can contain tokens/questions. Do not log request targets.
        pass

    def send(self, status, value, content_type="application/json; charset=utf-8"):
        content = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def check_access(self, api):
        host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise PermissionError("Only loopback hosts are allowed")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            raise PermissionError("Cross-origin requests are not allowed")
        if api:
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + self.server.token):
                raise PermissionError("Open the access link printed by cweb to connect")

    def do_GET(self):
        self.handle_request(False)

    def do_HEAD(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def route_remote(self, path, body=None):
        manager = self.server.remotes
        manager.read_only = self.server.operations.read_only
        manager.demo = self.server.operations.demo
        if path in {'/api/servers/ssh', '/api/servers/discover'}:
            from cslurm.web.discovery import ssh_aliases, discover
            if manager.demo:
                raise PermissionError('Demo console: SSH discovery disabled')
            if path.endswith('/ssh') and body is None:
                self.send(200, ssh_aliases())
            elif path.endswith('/discover') and body is not None:
                if not manager.discovery_slots.acquire(blocking=False):
                    raise BusyError('Environment detection is busy; wait before retrying')
                try:
                    self.send(200, discover(body.get('alias')))
                finally:
                    manager.discovery_slots.release()
            else:
                raise ValueError('Unsupported discovery method')
            return True
        if path == '/api/servers':
            self.send(200, manager.snapshot() if body is None else manager.action(body))
            return True
        identifier = self.headers.get('X-CSlurm-Server', 'local')
        if identifier != 'local' and path.startswith('/api/'):
            if manager.demo or (body is not None and manager.read_only):
                raise PermissionError('Read-only/demo console: remote operations disabled')
            status, result = manager.request(identifier, path, body)
            self.send(status, result)
            return True
        return False

    def handle_request(self, post):
        try:
            url = urlsplit(self.path)
            self.check_access(url.path.startswith("/api/"))
            if not post and self.route_remote(self.path):
                return
            if post:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Content-Type must be application/json")
                size = int(self.headers.get("Content-Length", "0"))
                maximum = 32768 if url.path.startswith("/api/settings") else 8192
                if not 0 < size <= maximum:
                    raise ValueError("Request body outside allowed size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if self.route_remote(self.path, body):
                    return
                if url.path in {"/api/settings", "/api/settings/test", "/api/settings/models"}:
                    ops = self.server.operations
                    if ops.read_only or ops.demo:
                        raise PermissionError("Read-only/demo mode: configuration changes and tests are disabled")
                    if body.get('background') and url.path != '/api/settings':
                        action = 'models' if url.path.endswith('/models') else 'model_test'
                        self.send(202, ops.start(action, body))
                        return
                    with ops.lock:
                        if ops.active or ops.stopping:
                            raise BusyError("An operation is running; wait before editing configuration")
                        if url.path == "/api/settings":
                            self.send(200, settings.save(body))
                        elif url.path == "/api/settings/models":
                            try:
                                self.send(200, settings.fetch_models(body))
                            except (ValueError, settings.SettingsConflict, ModelDiscoveryError):
                                raise
                            except Exception:
                                self.send(502, {"error": "获取模型列表失败，请检查配置和网络，或手动填写模型名。"})
                        else:
                            try:
                                result = settings.test_connection(body)
                            except (ValueError, settings.SettingsConflict):
                                raise
                            except Exception:
                                self.send(502, {"error": "连接测试失败或超时，请检查已保存的地址、凭据、模型和网络。"})
                                return
                            self.send(200, result)
                    return
                if url.path != "/api/operations":
                    raise KeyError("Endpoint not found")
                if self.server.database_error:
                    self.send(503, {"error": self.server.database_error})
                    return
                self.send(202, self.server.operations.start(body.get("action"), body))
                return
            query = parse_qs(url.query)
            param = lambda key, default="": query.get(key, [default])[0]
            if self.server.database_error and url.path.startswith("/api/") and url.path not in {"/api/overview", "/api/health"}:
                self.send(503, {"error": self.server.database_error})
                return
            if url.path == "/api/overview":
                ops = self.server.operations
                overview = data.overview() if not self.server.database_error else {
                    "total": None, "active": None, "completed": None, "failed": None,
                    "partitions": [], "states": {}, "updated_at": None,
                }
                self.send(200, {**overview, "host": socket.gethostname(), "root": str(root_dir()),
                                "read_only": ops.read_only, "allow_cancel": ops.allow_cancel,
                                "demo": ops.demo, "active_operation": ops.active,
                                "database_error": self.server.database_error})
            elif url.path == "/api/settings":
                self.send(200, settings.snapshot())
            elif url.path == '/api/health':
                from cslurm.doctor import health
                self.send(200, health())
            elif url.path == '/api/compare':
                self.send(200, data.compare(param('ids').split(',')))
            elif url.path == "/api/jobs":
                limit, offset = int(param("limit", "25")), int(param("offset", "0"))
                if not 1 <= limit <= 100 or not 0 <= offset <= 10000000 or len(url.query) > 4096:
                    raise ValueError("Invalid pagination or search")
                self.send(200, data.list_jobs(search=param("search"), state=param("state"), partition=param("partition"), limit=limit, offset=offset,
                                               since=param('since'), until=param('until'), cwd=param('cwd'), tag=param('tag')))
            elif url.path.startswith("/api/jobs/"):
                parts = url.path.split("/")
                if len(parts) == 4:
                    self.send(200, data.detail(parts[3]))
                elif len(parts) == 5 and parts[4] == "logs":
                    lines = int(param("lines", "200"))
                    if not 1 <= lines <= 1000:
                        raise ValueError("Invalid line count")
                    self.send(200, data.logs(parts[3], lines))
                else:
                    raise KeyError("Endpoint not found")
            elif url.path.startswith("/api/operations/"):
                self.send(200, self.server.operations.get(url.path.rsplit("/", 1)[-1]))
            elif url.path.startswith("/api/"):
                raise KeyError("Endpoint not found")
            else:
                name = "index.html" if url.path == "/" else url.path.removeprefix("/")
                path = (STATIC / name).resolve()
                if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                    raise KeyError("File not found")
                self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        except PermissionError as exc:
            self.send(403, {"error": str(exc)})
        except KeyError as exc:
            self.send(404, {"error": exc.args[0]})
        except BusyError as exc:
            self.send(409, {"error": str(exc)})
        except RemoteError as exc:
            self.send(502, {"error": str(exc)})
        except settings.SettingsConflict as exc:
            self.send(409, {"error": str(exc)})
        except ModelDiscoveryError as exc:
            self.send(502, {"error": str(exc), "code": exc.code})
        except (ValueError, TypeError):
            message = "配置无效，请检查接口格式、地址、环境变量名称、数值范围及连接必填项。" if self.path.startswith("/api/settings") else "Invalid request. Check job ID, confirmation, and input values."
            self.send(400, {"error": message})
        except sqlite3.Error:
            self.send(503, {"error": "Database unavailable. Check permissions and integrity on the server."})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.send(500, {"error": "Server error. Inspect the server environment; no operation was automatically retried."})
