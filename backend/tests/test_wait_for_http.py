import http.server
import socketserver
import subprocess
import threading
import time
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / 'scripts' / 'wait-for-http.sh'


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, format, *args):
        return


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def _start_server(port: int):
    server = ReusableTCPServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_wait_for_http_retries_until_success(free_tcp_port: int):
    def delayed_server_start():
        time.sleep(2)
        server = _start_server(free_tcp_port)
        servers.append(server)

    servers = []
    starter = threading.Thread(target=delayed_server_start, daemon=True)
    starter.start()

    result = subprocess.run(
        ['bash', str(SCRIPT_PATH), f'http://127.0.0.1:{free_tcp_port}/', '6', 'DelayedHTTP'],
        capture_output=True,
        text=True,
        check=False,
    )

    for server in servers:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0
    assert 'True' not in result.stdout
    assert 'False' not in result.stdout


def test_wait_for_http_times_out_cleanly(free_tcp_port: int):
    result = subprocess.run(
        ['bash', str(SCRIPT_PATH), f'http://127.0.0.1:{free_tcp_port}/', '2', 'MissingHTTP'],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert 'MissingHTTP failed health check' in result.stdout
    assert 'True' not in result.stdout
    assert 'False' not in result.stdout
