from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets
import time
from typing import Optional

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SessionModel, User
from app.utils.redis import redis_client


def hash_secret(secret: str) -> str:
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


DUMMY_PASSWORD_HASH = hash_secret("constant-time-dummy-password-placeholder")


@dataclass(frozen=True)
class RateLimitPolicy:
    namespace: str
    max_attempts: int = 5
    window_seconds: int = 900


LOGIN_RATE_LIMIT = RateLimitPolicy(namespace="auth:login_fail", max_attempts=5, window_seconds=900)
PIN_RATE_LIMIT = RateLimitPolicy(namespace="auth:pin_fail", max_attempts=5, window_seconds=900)

_LOCAL_ATTEMPTS: dict[str, tuple[int, float]] = {}


def _storage_key(policy: RateLimitPolicy, identifier: str) -> str:
    return f"{policy.namespace}:{identifier}"


def _get_local_attempts(key: str) -> int:
    entry = _LOCAL_ATTEMPTS.get(key)
    if not entry:
        return 0
    count, expires_at = entry
    if time.monotonic() >= expires_at:
        _LOCAL_ATTEMPTS.pop(key, None)
        return 0
    return count


async def is_rate_limited(policy: RateLimitPolicy, identifier: str) -> bool:
    key = _storage_key(policy, identifier)
    if _get_local_attempts(key) >= policy.max_attempts:
        return True
    try:
        raw = await redis_client.get(key)
        if raw is not None and int(raw) >= policy.max_attempts:
            return True
    except Exception:
        pass
    return False


async def record_failed_attempt(policy: RateLimitPolicy, identifier: str) -> int:
    key = _storage_key(policy, identifier)
    now = time.monotonic()
    entry = _LOCAL_ATTEMPTS.get(key)
    if not entry or now >= entry[1]:
        count = 1
        expires_at = now + policy.window_seconds
    else:
        count = entry[0] + 1
        expires_at = entry[1]
    _LOCAL_ATTEMPTS[key] = (count, expires_at)

    try:
        redis_count = await redis_client.incr(key)
        if redis_count == 1:
            await redis_client.expire(key, policy.window_seconds)
        return max(count, int(redis_count))
    except Exception:
        return count


async def clear_failed_attempts(policy: RateLimitPolicy, identifier: str) -> None:
    key = _storage_key(policy, identifier)
    _LOCAL_ATTEMPTS.pop(key, None)
    try:
        await redis_client.delete(key)
    except Exception:
        pass


def verify_secret_constant_time(secret: str, secret_hash: Optional[str]) -> bool:
    target_hash = secret_hash if secret_hash is not None else DUMMY_PASSWORD_HASH
    try:
        return bcrypt.checkpw(secret.encode("utf-8"), target_hash.encode("utf-8"))
    except ValueError:
        return False


async def verify_transaction_pin(user: User, pin: Optional[str]) -> None:
    identifier = str(user.id)
    if await is_rate_limited(PIN_RATE_LIMIT, identifier):
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak percobaan PIN gagal. Silakan coba lagi nanti.",
        )

    if not pin or not pin.isdigit() or len(pin) != 6:
        raise HTTPException(status_code=400, detail="PIN transaksi 6 digit wajib diisi")

    if not verify_secret_constant_time(pin, user.pin_hash):
        await record_failed_attempt(PIN_RATE_LIMIT, identifier)
        raise HTTPException(status_code=401, detail="PIN transaksi salah")

    await clear_failed_attempts(PIN_RATE_LIMIT, identifier)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_session_token(raw_token)


def prune_expired_sessions(db: Session) -> int:
    deleted = db.query(SessionModel).filter(SessionModel.expires_at < datetime.now(timezone.utc)).delete()
    if deleted:
        db.commit()
    return deleted


async def get_current_session(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> SessionModel:
    raw_token = authorization.removeprefix("Bearer ").strip()
    token_hash = hash_session_token(raw_token)
    session = db.query(SessionModel).filter(SessionModel.session_id == token_hash).first()
    if not session:
        session = db.query(SessionModel).filter(SessionModel.session_id == raw_token).first()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    return session


async def get_current_user(
    session: SessionModel = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> User:
    user = db.query(User).filter(User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    return user
