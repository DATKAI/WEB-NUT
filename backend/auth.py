import secrets
import time

# Простое хранилище сессий в памяти (токен → user dict)
_sessions: dict[str, dict] = {}
SESSION_TTL = 86400  # 24 часа


def create_session(user: dict) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {
        "user": user,
        "expires": time.time() + SESSION_TTL
    }
    return token


def get_session(token: str) -> dict | None:
    if not token:
        return None
    session = _sessions.get(token)
    if not session:
        return None
    if time.time() > session["expires"]:
        del _sessions[token]
        return None
    return session["user"]


def delete_session(token: str):
    _sessions.pop(token, None)


def cleanup_sessions():
    now = time.time()
    expired = [t for t, s in _sessions.items() if now > s["expires"]]
    for t in expired:
        del _sessions[t]
