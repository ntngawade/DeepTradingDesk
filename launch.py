"""Open the real application backend, avoiding static preview servers."""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
import socket
from app_version import APP_VERSION

ROOT = Path(__file__).resolve().parent


def backend_info(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/app/status', timeout=1) as response:
            data = json.load(response)
        if data.get('application') == 'DeepTradingDesk' and Path(data.get('workspace', '')).resolve() == ROOT:
            return data
        return None
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/dhan/status', timeout=1) as response:
            data = json.load(response)
        if data.get('executionMode') == 'paper' and 'hasSession' in data:
            return data
    except Exception:
        pass
    return None


def is_backend(port):
    data = backend_info(port)
    return bool(data and data.get('backendVersion', 0) >= APP_VERSION)


def select_backend():
    port = 8081
    info = backend_info(port)
    if info:
        if info.get('backendVersion', 0) >= APP_VERSION:
            return port
        raise RuntimeError('An older Deep Trading Desk backend is running on port 8081. Close that server, then start this launcher again.')
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8081))
    except OSError as error:
        raise RuntimeError('Port 8081 is already in use. Close its current application, then restart Deep Trading Desk.') from error
    process = subprocess.Popen(
        [sys.executable, str(ROOT / 'server.py'), '--port', str(port)], cwd=str(ROOT),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    for _ in range(40):
        if is_backend(port):
            return port
        if process.poll() is not None:
            break
        time.sleep(0.25)
    raise RuntimeError('Cannot start Deep Trading Desk on port 8081. Run python server.py --port 8081 in PowerShell to inspect the error.')


if __name__ == '__main__':
    port = select_backend()
    url = f'http://127.0.0.1:{port}'
    print(f'Opening Deep Trading Desk: {url}')
    webbrowser.open(url)
