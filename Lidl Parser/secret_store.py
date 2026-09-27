"""Encrypted-at-rest storage for the Lidl password.

The password is Fernet-encrypted with a machine-local key: ``LIDL_KEY``
env wins, otherwise ``data/.lidl-key`` (created once, chmod 600, never
committed — ``data/`` is gitignored). Legacy plaintext values saved by
older versions keep working and are re-encrypted on the next save.
"""
import os

import db as dbmod

KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".lidl-key")
PREFIX = "enc$"


def _load_key():
    from cryptography.fernet import Fernet
    env = (os.environ.get("LIDL_KEY") or "").strip()
    if env:
        return env.encode()
    try:
        with open(KEY_PATH, "rb") as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def _raw(key):
    import settings_store as ss
    return ss.get(key, "")


def get_secret(key="LIDL_PASS", default=""):
    raw = _raw(key)
    if not raw:
        return default
    if not raw.startswith(PREFIX):
        return raw  # legacy plaintext
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_load_key()).decrypt(raw[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        return default


def set_secret(key, value):
    from cryptography.fernet import Fernet
    import settings_store as ss
    token = Fernet(_load_key()).encrypt((value or "").encode()).decode()
    ss.set(key, PREFIX + token)


def clear_secret(key):
    import settings_store as ss
    ss.set(key, "")
