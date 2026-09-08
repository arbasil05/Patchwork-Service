import hmac
import secrets
import time
import os
import bcrypt

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from dependencies.auth import create_session, get_current_admin, invalidate_session

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Rate limiter — in-memory, per-IP, sliding fixed window
# Format: { ip: (attempt_count, window_start_timestamp) }
# ---------------------------------------------------------------------------
_FAILED_ATTEMPTS: dict = {}
_MAX_ATTEMPTS = 5
_LOCKOUT_WINDOW = 300  # seconds (5 minutes)


def _check_rate_limit(ip: str) -> None:
    """Raise 429 if the IP has exceeded MAX_ATTEMPTS within the lockout window."""
    now = time.time()
    record = _FAILED_ATTEMPTS.get(ip)
    if record is None:
        return
    attempts, window_start = record
    if attempts >= _MAX_ATTEMPTS and (now - window_start) < _LOCKOUT_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )


def _increment_failed(ip: str) -> None:
    now = time.time()
    record = _FAILED_ATTEMPTS.get(ip)
    if record is None or (now - record[1]) >= _LOCKOUT_WINDOW:
        # Start a fresh window
        _FAILED_ATTEMPTS[ip] = (1, now)
    else:
        _FAILED_ATTEMPTS[ip] = (record[0] + 1, record[1])


def _reset_failed(ip: str) -> None:
    _FAILED_ATTEMPTS.pop(ip, None)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login")
def login(request: Request, response: Response, body: LoginRequest):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    admin_user = os.getenv("ADMIN_USERNAME")
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")

    if not admin_user or not admin_hash:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin credentials not configured",
        )

    # Constant-time username comparison
    is_valid_user = hmac.compare_digest(
        body.username.encode("utf-8"),
        admin_user.encode("utf-8"),
    )

    # bcrypt constant-time password check
    is_valid_pass = False
    try:
        is_valid_pass = bcrypt.checkpw(
            body.password.encode("utf-8"),
            admin_hash.encode("utf-8"),
        )
    except Exception:
        pass  # Malformed hash → treat as failure, not a 500

    if not (is_valid_user and is_valid_pass):
        _increment_failed(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    _reset_failed(client_ip)

    token = create_session()

    is_secure = os.getenv("ENVIRONMENT", "development").lower() == "production"
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        secure=is_secure,
        samesite="strict",
        max_age=12 * 3600,
    )

    return {"message": "Logged in successfully"}


@router.post("/logout")
def logout(response: Response, request: Request, _admin=Depends(get_current_admin)):
    """
    Requires a valid session — consistent with the rest of the admin boundary.
    Invalidates the token server-side and clears the cookie.
    """
    token = request.cookies.get("admin_session")
    if token:
        invalidate_session(token)
    response.delete_cookie("admin_session")
    return {"message": "Logged out successfully"}


@router.get("/me")
def get_me(admin: str = Depends(get_current_admin)):
    return {"username": admin, "role": "admin"}
