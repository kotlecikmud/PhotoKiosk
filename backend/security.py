"""Secure password hashing and verification helpers using bcrypt directly."""
import bcrypt


def hash_password(password: str) -> str:
    """Hashes a password using bcrypt (truncating to 72 bytes as per bcrypt specification)."""
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a stored bcrypt hash."""
    try:
        pwd_bytes = password.encode("utf-8")[:72]
        hash_bytes = hashed.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False
