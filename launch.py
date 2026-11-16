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
    outdated=[]
    for port in range(8080, 8091):
        info=backend_info(port)
        if info and info.get('backendVersion',0)>=APP_VERSION:
            return port
        if info:
            outdated.append(port)
    if outdated:
        raise RuntimeError('An older Deep Trading Desk backend is still running on port(s) '+', '.join(map(str,outdated))+'. Restart that backend before opening the app; the launcher will not silently switch to a different storage origin.')
    for port in range(8080, 8091):
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
        except OSError:
            continue
        process = subprocess.Popen(
            [sys.executable, str(ROOT / 'server.py'), '--port', str(port)],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        for _ in range(30):
            if is_backend(port):
                return port
            if process.poll() is not None:
                break
            time.sleep(0.2)
    raise RuntimeError('Cannot start the app. Run python server.py --port 8081 in a terminal to inspect the error.')


def preserve_compatibility_address(port):
    """Preserve existing 8081 browser storage without starting another engine."""
    if port != 8080:
        return
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8081))
    except OSError:
        return
    subprocess.Popen(
        [sys.executable, str(ROOT / 'compat_server.py'), '--port', '8081', '--upstream', str(port)],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


if __name__ == '__main__':
    port = select_backend()
    preserve_compatibility_address(port)
    url = f'http://127.0.0.1:{port}'
    print(f'Opening Deep Trading Desk: {url}')
    webbrowser.open(url)
