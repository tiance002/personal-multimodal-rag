"""Fixed WeKnora utils/crypto.go AES-256-GCM stored-secret wire format."""
import base64
import os

PREFIX = 'enc:v1:'


class SecretCipherError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError:
        raise SecretCipherError('STORED_SECRET_RUNTIME_UNAVAILABLE') from None


class SecretCipher:
    def __init__(self, key: bytes | None):
        if key is not None and (not isinstance(key,bytes) or len(key) != 32):
            raise ValueError('STORED_SECRET_KEY_INVALID')
        self._key = key

    def encrypt(self, value: str) -> str:
        if not value or self._key is None or value.startswith(PREFIX):
            return value
        AESGCM = _aesgcm()
        nonce = os.urandom(12)
        payload = nonce + AESGCM(self._key).encrypt(nonce,value.encode('utf-8'),None)
        return PREFIX + base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')

    def decrypt(self, value: str, *, strict: bool = True) -> str:
        if not value or not value.startswith(PREFIX):
            return value
        # Import failures are runtime faults, not evidence of a damaged secret.
        AESGCM = _aesgcm()
        try:
            if self._key is None:
                raise ValueError('STORED_SECRET_KEY_REQUIRED')
            encoded = value[len(PREFIX):]
            payload = base64.b64decode(encoded + '='*((-len(encoded))%4),altchars=b'-_',validate=True)
            if len(payload) < 12:
                raise ValueError('STORED_SECRET_INVALID')
            return AESGCM(self._key).decrypt(payload[:12],payload[12:],None).decode('utf-8')
        except Exception:
            if strict:
                raise SecretCipherError('STORED_SECRET_DECRYPT_FAILED') from None
            return ''
