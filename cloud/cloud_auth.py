"""Who may publish to this relay, and who may administer it.

Two unrelated credentials with the same home on disk, so they share a module:

* **Relay keys** — one per organizer, handed out from the admin panel. A Pi sends
  its key on `register` and the relay accepts or rejects the connection.
* **The admin login** — a single username and password guarding `/admin`. Seeded
  from the environment on first run, then owned by `credentials.json` so a
  password change survives a redeploy.

The failed-sign-in throttle lives here too, because it is the other half of what
makes one password on the open internet defensible.
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import threading
import time

from fastapi import HTTPException, Request

import cloud_paths
from cloud_paths import atomic_write


def load_keys():
    try:
        with open(cloud_paths.KEYS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_keys(keys):
    atomic_write(cloud_paths.KEYS_FILE, json.dumps(keys, indent=2))


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return base64.b64encode(dk).decode(), salt


def load_creds():
    try:
        with open(cloud_paths.CREDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # First run on a new install: seed from the environment the installer set, then
    # own the value from here on so a password changed in /admin survives a redeploy.
    # Not a migration — this is the only path by which an admin login is ever created.
    user     = os.environ.get('ADMIN_USER', 'admin')
    password = os.environ.get('ADMIN_PASSWORD', '')
    pw_hash, salt = hash_password(password)
    creds = {'user': user, 'password_hash': pw_hash, 'salt': salt}
    save_creds(creds)
    return creds


def save_creds(creds):
    atomic_write(cloud_paths.CREDS_FILE, json.dumps(creds, indent=2))


# What the Appearance tab accepts. The logo is drawn by a browser `<img>`, so the
# list is the formats every current browser renders; the icon is also fed to the web
# manifest, which names `image/png` for both sizes, so it stays PNG-only.


# Failed-sign-in throttle. This panel is on the open internet behind one password
# and nothing else — fail2ban here only watches sshd — so without a limit an
# attacker gets unlimited guesses at it. The window is generous enough that an
# operator mistyping a password on meet day never notices.
#
# It also caps a second cost: every guess runs PBKDF2 at 100k iterations, so an
# unauthenticated flood of them is a CPU exhaustion attack on the box serving the
# meet. Locked-out requests are refused *before* the hash is computed.
_ADMIN_FAIL_MAX    = 10
_ADMIN_FAIL_WINDOW = datetime.timedelta(minutes=15).total_seconds()
_admin_fails       = {}                 # ip -> [count, first_failure_monotonic]
_admin_fails_lock  = threading.Lock()


def _admin_client_ip(request):
    """The caller's address. uvicorn rewrites this from X-Forwarded-For for the
    proxies named in FORWARDED_ALLOW_IPS (see docker-compose.yml), so behind Caddy
    it is the real client rather than the compose bridge."""
    return request.client.host if request.client else '?'


def _admin_locked(ip):
    with _admin_fails_lock:
        entry = _admin_fails.get(ip)
        if not entry:
            return False
        count, first = entry
        if time.monotonic() - first > _ADMIN_FAIL_WINDOW:
            del _admin_fails[ip]          # window elapsed — start clean
            return False
        return count >= _ADMIN_FAIL_MAX


def _admin_note_failure(ip):
    now = time.monotonic()
    with _admin_fails_lock:
        entry = _admin_fails.get(ip)
        if entry and now - entry[1] <= _ADMIN_FAIL_WINDOW:
            entry[0] += 1
        else:
            _admin_fails[ip] = [1, now]
        # Bound the dict: an attacker rotating source addresses must not be able to
        # grow it without end. Drop whatever has aged out of the window.
        if len(_admin_fails) > 1024:
            for k in [k for k, v in _admin_fails.items()
                      if now - v[1] > _ADMIN_FAIL_WINDOW]:
                del _admin_fails[k]


def _admin_note_success(ip):
    with _admin_fails_lock:
        _admin_fails.pop(ip, None)


def check_admin(request):
    hdr = request.headers.get('Authorization', '')
    if not hdr.startswith('Basic '):
        return False
    try:
        user, _, pw = base64.b64decode(hdr[6:]).decode().partition(':')
    except Exception:
        return False
    creds = load_creds()
    # compare_digest on the username too: `!=` returns on the first differing
    # character, which given enough attempts reveals it.
    ok_user = hmac.compare_digest(user, creds['user'])
    pw_hash, _ = hash_password(pw, creds['salt'])
    ok_pw = hmac.compare_digest(pw_hash, creds['password_hash'])
    return ok_user and ok_pw


def require_admin(request: Request):
    ip = _admin_client_ip(request)
    if _admin_locked(ip):
        # 429, not 401: a browser answers 401 by re-prompting, which would walk the
        # operator into retrying against a lock that only their waiting clears.
        raise HTTPException(status_code=429,
                            detail='Too many failed sign-in attempts. Try again later.',
                            headers={'Retry-After': str(int(_ADMIN_FAIL_WINDOW))})
    if not check_admin(request):
        _admin_note_failure(ip)
        raise HTTPException(status_code=401, detail='Authentication required',
                            headers={'WWW-Authenticate': 'Basic realm="Splouch Admin"'})
    _admin_note_success(ip)
