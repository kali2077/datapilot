"""认证与加密：PBKDF2 口令哈希、JWT、Fernet 凭据加密。"""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

import jwt
from cryptography.fernet import Fernet

from . import config

_ROUNDS = 120_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ROUNDS)
    return f"pbkdf2${_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id), "username": username, "role": role,
        "exp": int(time.time()) + config.JWT_EXPIRE_MINUTES * 60,
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


_f = Fernet(config.FERNET_KEY.encode())


def encrypt_secret(plain: str) -> str:
    return _f.encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    return _f.decrypt(cipher.encode()).decode()


def sign_payload(payload: dict, secret: str) -> str:
    """API 网关签名：HMAC-SHA256(secret, app_key + timestamp + body)"""
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def hash_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def gen_secret(nbytes: int = 24) -> str:
    return base64.urlsafe_b64encode(os.urandom(nbytes)).decode().rstrip("=")
