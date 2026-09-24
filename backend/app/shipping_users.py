"""Warehouse profiles for shipping attribution; Planner token auth still applies."""
import json
import uuid
from pathlib import Path
from threading import RLock

USERS_PATH = Path(__file__).resolve().parent / 'data' / 'shipping_users.json'
_lock = RLock()


def load_users():
    with _lock:
        if not USERS_PATH.exists():
            return []
        payload = json.loads(USERS_PATH.read_text(encoding='utf-8'))
        return sorted(payload['users'], key=lambda user: user['name'].casefold())


def save_user(raw):
    name = str(raw.get('name') or '').strip()
    initials = str(raw.get('initials') or '').strip().upper()
    if not name or len(name) > 120 or len(initials) > 12:
        raise ValueError('Enter a name (up to 120 characters) and optional initials (up to 12).')
    with _lock:
        users = load_users()
        existing = next((u for u in users if u['name'].casefold() == name.casefold()), None)
        if existing:
            return existing
        user = {'id': str(uuid.uuid4()), 'name': name, 'initials': initials or ''.join(p[0] for p in name.split())[:4].upper()}
        users.append(user)
        USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = USERS_PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 1, 'users': users}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(USERS_PATH)
        return user


def resolve_user(user_id):
    return next((u for u in load_users() if u['id'] == user_id), None)
