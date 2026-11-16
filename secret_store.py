"""Windows-account-bound encrypted storage; no plaintext token is written."""
import ctypes
import json
import os
import base64
from pathlib import Path


def windows_crypt(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Secure remembering is available only on Windows.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming = Blob(len(data), buffer)
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('Windows could not access the encrypted credentials for this Windows account.')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def cloud_crypt(data, decrypt=False):
    """AES-GCM credential encryption for a Linux cloud host, keyed outside the repo."""
    encoded_key = os.environ.get('DESK_CREDENTIAL_KEY', '')
    try:
        key = base64.b64decode(encoded_key, altchars=b'-_', validate=True)
    except (ValueError, TypeError):
        key = b''
    if len(key) != 32:
        raise ValueError('Cloud credential storage needs a base64 32-byte DESK_CREDENTIAL_KEY.')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise ValueError('Install the cryptography package to use encrypted cloud credential storage.') from None
    cipher = AESGCM(key)
    if decrypt:
        if not data.startswith(b'DTD1') or len(data) < 32:
            raise ValueError('Saved cloud credentials are invalid. Reconnect with a fresh Dhan token.')
        return cipher.decrypt(data[4:16], data[16:], None)
    nonce = os.urandom(12)
    return b'DTD1' + nonce + cipher.encrypt(nonce, data, None)


class SecretStore:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, client_id, token):
        payload = json.dumps({'clientId': client_id, 'token': token}).encode()
        encrypted = windows_crypt(payload) if os.name == 'nt' else cloud_crypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_bytes(encrypted)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self):
        if not self.path.exists():
            return None
        encoded = self.path.read_bytes()
        if os.name == 'nt':
            data = json.loads(windows_crypt(encoded, decrypt=True))
        else:
            data = json.loads(cloud_crypt(encoded, decrypt=True))
        if not isinstance(data, dict) or not isinstance(data.get('clientId'), str) or not isinstance(data.get('token'), str):
            raise ValueError('Saved credentials are invalid. Reconnect with a fresh Dhan token.')
        return data

    def forget(self):
        self.path.unlink(missing_ok=True)
