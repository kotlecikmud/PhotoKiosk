"""Admin authentication, session token management, and login rate limiting."""
import secrets
import time
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.database import SystemSetting
from backend.security import verify_password, hash_password

ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}
FAILED_ATTEMPTS: Dict[str, Dict[str, Any]] = {}

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 900  # 15 minutes
SESSION_TTL_SECONDS = 3600  # 1 hour


def is_rate_limited(client_ip: str) -> Tuple[bool, int]:
    """Returns (is_locked, remaining_seconds)."""
    now = time.time()
    info = FAILED_ATTEMPTS.get(client_ip)
    if not info:
        return False, 0

    if info["count"] >= MAX_FAILED_ATTEMPTS:
        elapsed = now - info["last_attempt"]
        if elapsed < LOCKOUT_DURATION_SECONDS:
            return True, int(LOCKOUT_DURATION_SECONDS - elapsed)
        else:
            # Lockout expired, reset
            FAILED_ATTEMPTS.pop(client_ip, None)
            return False, 0
    return False, 0


def record_failed_attempt(client_ip: str):
    now = time.time()
    if client_ip not in FAILED_ATTEMPTS:
        FAILED_ATTEMPTS[client_ip] = {"count": 1, "last_attempt": now}
    else:
        FAILED_ATTEMPTS[client_ip]["count"] += 1
        FAILED_ATTEMPTS[client_ip]["last_attempt"] = now


def record_successful_login(client_ip: str):
    FAILED_ATTEMPTS.pop(client_ip, None)


def create_session(user_role: str = "admin") -> str:
    token = secrets.token_hex(32)
    ACTIVE_SESSIONS[token] = {
        "role": user_role,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    return token


def validate_session(token: Optional[str]) -> bool:
    if not token or token not in ACTIVE_SESSIONS:
        return False
    session = ACTIVE_SESSIONS[token]
    if time.time() > session["expires_at"]:
        ACTIVE_SESSIONS.pop(token, None)
        return False
    # Refresh expiration on active use
    session["expires_at"] = time.time() + SESSION_TTL_SECONDS
    return True


def revoke_session(token: str):
    ACTIVE_SESSIONS.pop(token, None)
