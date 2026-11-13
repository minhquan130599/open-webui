"""Admin key (MONITOR_REVEAL_KEY) + one-time code (Google Authenticator / TOTP) for changing settings from the monitor page.

The TOTP secret is created once from the server terminal (see totp.py), stored only in the database,
encrypted with a key derived from the admin key, and never shown on the web page. Each code can be used
once (the last accepted time step is stored).
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Optional

import pyotp
from cryptography.fernet import Fernet, InvalidToken

# The key that unlocks message content (MONITOR_REVEAL_KEY, else MONITOR_API_KEY) also unlocks settings, always
# together with a Google Authenticator code. MONITOR_ADMIN_KEY, if set, still takes precedence.
ADMIN_KEY = (
    os.getenv('MONITOR_ADMIN_KEY', '') or os.getenv('MONITOR_REVEAL_KEY', '') or os.getenv('MONITOR_API_KEY', '')
)
TOTP_KEY = 'monitor.totp'  # config row: {'enc': <Fernet token>, 'created_at': ts, 'last_step': int}
ISSUER = os.getenv('MONITOR_TOTP_ISSUER', 'NCS AI Monitor')


def _fernet(admin_key: str) -> Fernet:
    digest = hashlib.sha256(b'ncs-monitor-totp:' + admin_key.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def check_admin_key(key: str) -> bool:
    return bool(ADMIN_KEY) and bool(key) and hmac.compare_digest(key, ADMIN_KEY)


def encrypt_secret(secret: str, admin_key: str = '') -> str:
    return _fernet(admin_key or ADMIN_KEY).encrypt(secret.encode('utf-8')).decode('ascii')


def decrypt_secret(token: str, admin_key: str = '') -> Optional[str]:
    try:
        return _fernet(admin_key or ADMIN_KEY).decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        return None


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, name: str = 'admin') -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name=ISSUER)


def verify_code(secret: str, code: str, last_step: int) -> Optional[int]:
    """The time step the code belongs to (one step either side allowed), or None. Codes of a step already
    used (<= last_step) are rejected, so a code works once."""
    code = (code or '').strip().replace(' ', '')
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // totp.interval
    for step in (now_step - 1, now_step, now_step + 1):
        if step > last_step and hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None
