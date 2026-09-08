import hmac
import secrets
import time
import os
from fastapi import Request, HTTPException, status

# Fully encapsulated session store — never exported or mutated externally.
# Key: token string, Value: expiry unix timestamp (float)
# NOTE: In-memory only. Replace with Redis for multi-worker or persistent sessions.
_sessions: dict = {}


def create_session() -> str:
    """Generate a CSPRNG token, store it server-side, and return it."""
    token = secrets.token_urlsafe(32)
    expiry = time.time() + (12 * 3600)  # 12-hour TTL
    _sessions[token] = expiry
    return token


def invalidate_session(token: str) -> None:
    """Remove a session token from the store (safe to call with unknown tokens)."""
    _sessions.pop(token, None)


def _purge_expired() -> None:
    """Remove all expired sessions. Called lazily on each auth check."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if exp < now]
    for t in expired:
        del _sessions[t]


def get_current_admin(request: Request) -> str:
    """
    FastAPI dependency. Extracts the session cookie, validates it via a direct
    O(1) dict lookup, and returns the admin username. Raises 401 on any failure.

    Security note: token_urlsafe(32) produces 256-bit random tokens. A direct
    dict lookup is safe here — constant-time comparison is not required because
    the token IS the secret and its length/existence is already revealed by the
    HTTP response code. Iterating all sessions with hmac.compare_digest (the
    previous approach) was slower, O(n), and created a dict-mutation race.
    """
    token = request.cookies.get("admin_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    _purge_expired()

    expiry = _sessions.get(token)
    if expiry is None or expiry < time.time():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    return os.getenv("ADMIN_USERNAME", "admin")
