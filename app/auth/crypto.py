import base64
import hashlib
import os
from typing import Dict, Any, Union
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
IV_BYTES = 12


def derive_key(secret: Union[str, bytes]) -> bytes:
    if not secret:
        raise ValueError("Encryption secret must not be empty")
    b = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(b) == KEY_BYTES:
        return b
    if len(b) == KEY_BYTES * 2:
        try:
            return bytes.fromhex(b.decode("utf-8"))
        except ValueError:
            pass
    return hashlib.sha256(b).digest()


def encrypt_string(plaintext: str, secret: Union[str, bytes]) -> Dict[str, str]:
    key = derive_key(secret)
    aesgcm = AESGCM(key)
    iv = os.urandom(IV_BYTES)
    sealed = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # sealed contains ciphertext + 16-byte tag
    ciphertext = sealed[:-16]
    auth_tag = sealed[-16:]

    return {
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "authTag": base64.b64encode(auth_tag).decode("ascii"),
        "algorithm": "aes-256-gcm",
    }


def decrypt_string(payload: Dict[str, str], secret: Union[str, bytes]) -> str:
    if not payload.get("ciphertext") or not payload.get("iv") or not payload.get("authTag"):
        raise ValueError("Malformed encrypted payload")

    key = derive_key(secret)
    aesgcm = AESGCM(key)
    iv = base64.b64decode(payload["iv"])
    ciphertext = base64.b64decode(payload["ciphertext"])
    auth_tag = base64.b64decode(payload["authTag"])

    sealed = ciphertext + auth_tag
    decrypted_bytes = aesgcm.decrypt(iv, sealed, None)
    return decrypted_bytes.decode("utf-8")


def mask_secret(value: str, visible: int = 4) -> str:
    if not value or not isinstance(value, str):
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    mask_len = min(8, len(value) - visible)
    return ("*" * mask_len) + value[-visible:]
