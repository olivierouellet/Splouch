"""Who may publish to this relay, and who may administer it.

The control plane's half of the relay (`cloud_control`), on its Postgres
(`cloud_db`). Workers never import this: they ask the control plane over its
internal API.

* **Organizers** — one relay key each, handed out from the admin panel, with the
  country, state/province and region the admin records for them
  (docs/architecture/scaling.md). A Pi sends its key on `register`; the worker
  asks the control plane, which accepts or rejects it.
* **Other admin users** — each with their own login and only some of the panel's
  tabs (`CLOUD_ROLES`). The login above is the owner's and always has every tab.
* **The admin login and server settings** — the owner's username and password
  guarding `/admin`, and the settings the panel edits (picker appearance, the analytics
  switch, the public-page locale). Seeded from the environment on first run, then
  owned by the database so a password change survives a redeploy. Read and
  written together as one dict, the shape `credentials.json` had, so the panel's
  code reads the same as it did.

The failed-sign-in throttle lives here too, because it is the other half of what
makes one password on the open internet defensible.
"""

import base64
import datetime
import hashlib
import hmac
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb

import cloud_db

_LOGIN_FIELDS = ("user", "password_hash", "salt")
_ORG_FIELDS = ("name", "active", "country", "province", "region")


# ── Organizers ─────────────────────────────────────────────────────────────────


def _org_row(r):
    """An organizer as the admin panel and the backups have always spelled it."""
    return {
        "organizer": r["name"],
        "created": r["created"].isoformat(),
        "active": r["active"],
        "country": r["country"],
        "province": r["province"],
        "region": r["region"] or "",
        # What the organizer's Pi says, when it differs from the record above.
        "reported": (
            {"country": r["reported_country"], "province": r["reported_province"]}
            if (r["reported_country"] or r["reported_province"])
            and (r["reported_country"], r["reported_province"])
            != (r["country"], r["province"])
            else None
        ),
    }


def load_keys():
    """Every organizer, keyed by relay key, oldest first. Not the test one: its key
    is the control plane's own, and is neither handed out nor backed up."""
    with cloud_db.conn() as c:
        rows = c.execute(
            "SELECT * FROM organizers WHERE NOT test ORDER BY created, name"
        ).fetchall()
    return {r["key"]: _org_row(r) for r in rows}


TEST_ORGANIZER = "Splouch Test"


def test_organizer_key():
    """The key the control plane's test meets publish under (cloud_testmeets),
    made the first time it is asked for. No region: any node may carry them."""
    with cloud_db.conn() as c:
        row = c.execute("SELECT key FROM organizers WHERE test").fetchone()
        if row:
            return row["key"]
        key = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO organizers (key, name, test) VALUES (%s, %s, true)",
            (key, TEST_ORGANIZER),
        )
    return key


def organizer(key):
    """One organizer's row, or None. The raw row: `name`, `legacy_meet_id`, …"""
    if not key:
        return None
    with cloud_db.conn() as c:
        return c.execute("SELECT * FROM organizers WHERE key = %s", (key,)).fetchone()


def regions():
    with cloud_db.conn() as c:
        return c.execute("SELECT code, name FROM regions ORDER BY code").fetchall()


def add_organizer(name, country="", province="", region=None):
    """Create an organizer with a fresh random key; returns the key."""
    key = secrets.token_urlsafe(32)
    with cloud_db.conn() as c:
        c.execute(
            "INSERT INTO organizers (key, name, country, province, region) "
            "VALUES (%s, %s, %s, %s, %s)",
            (key, name, country, province, region or None),
        )
    return key


def update_organizer(key, **fields):
    """Change some of an organizer's fields (`name`, `active`, `country`, …)."""
    fields = {k: v for k, v in fields.items() if k in _ORG_FIELDS}
    if not fields:
        return
    if "region" in fields:
        fields["region"] = fields["region"] or None
    sets = ", ".join(f"{k} = %s" for k in fields)
    with cloud_db.conn() as c:
        c.execute(
            f"UPDATE organizers SET {sets} WHERE key = %s", (*fields.values(), key)
        )


def delete_organizer(key):
    with cloud_db.conn() as c:
        c.execute("DELETE FROM organizers WHERE key = %s", (key,))


def report_location(key, country, province):
    """Where the organizer says it is based, from its Pi. Beside the admin's record,
    never over it: `/admin` flags a difference for the admin to accept."""
    with cloud_db.conn() as c:
        c.execute(
            "UPDATE organizers SET reported_country = %s, reported_province = %s "
            "WHERE key = %s",
            (country, province, key),
        )


def accept_location(key):
    """Take the organizer's own location as the record. The region stays as set."""
    with cloud_db.conn() as c:
        c.execute(
            "UPDATE organizers SET country = reported_country, "
            "province = reported_province WHERE key = %s AND "
            "(reported_country <> '' OR reported_province <> '')",
            (key,),
        )


def legacy_meet_id(key):
    """The one meet id a relay with no `meet_uid` publishes under, minted once."""
    with cloud_db.conn() as c:
        row = c.execute(
            "SELECT legacy_meet_id FROM organizers WHERE key = %s", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["legacy_meet_id"]:
            return row["legacy_meet_id"]
        meet_id = secrets.token_urlsafe(8)
        c.execute(
            "UPDATE organizers SET legacy_meet_id = %s WHERE key = %s", (meet_id, key)
        )
        return meet_id


def restore_keys(keys):
    """Upsert a backup's organizers. Ones not in the backup are left alone.

    Accepts the `keys.json` shape, old and new: a pre-scaling backup has no
    country, province or region, and keeps a no-uid relay's meet id as `meet_id`.
    """
    known = {r["code"] for r in regions()}
    with cloud_db.conn() as c:
        for key, info in keys.items():
            if not isinstance(info, dict):
                continue
            created = info.get("created") or datetime.date.today().isoformat()
            region = info.get("region") or None
            c.execute(
                "INSERT INTO organizers "
                "(key, name, created, active, country, province, region, legacy_meet_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, "
                "created = EXCLUDED.created, active = EXCLUDED.active, "
                "country = EXCLUDED.country, province = EXCLUDED.province, "
                "region = EXCLUDED.region, "
                "legacy_meet_id = COALESCE(EXCLUDED.legacy_meet_id, organizers.legacy_meet_id)",
                (
                    key,
                    str(info.get("organizer", "")),
                    created,
                    bool(info.get("active", False)),
                    str(info.get("country", "")),
                    str(info.get("province", "")),
                    region if region in known else None,
                    info.get("meet_id") or None,
                ),
            )


# ── Admin login and server settings ────────────────────────────────────────────


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return base64.b64encode(dk).decode(), salt


def load_creds():
    """The admin login and every server setting, as one dict."""
    with cloud_db.conn() as c:
        admin = c.execute("SELECT * FROM admin WHERE id = 1").fetchone()
        settings = {
            r["name"]: r["value"]
            for r in c.execute("SELECT name, value FROM settings").fetchall()
        }
    if admin is None:
        # First run on a new install: seed from the environment the installer set,
        # then own the value from here on so a password changed in /admin survives
        # a redeploy. The only path by which an admin login is ever created.
        user = os.environ.get("ADMIN_USER", "admin")
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            # Never seeded empty: that is a login of `admin` and nothing, on the open
            # internet, kept for good. An unset or mangled ADMIN_PASSWORD (compose
            # expands `$…` in an unquoted .env value) locks /admin behind a password
            # nobody knows instead, and the next start seeds again once it is fixed.
            print(
                "[auth] ADMIN_PASSWORD is empty — /admin stays locked until it is "
                "set in cloud/.env and the control plane restarted",
                flush=True,
            )
            pw_hash, salt = hash_password(secrets.token_urlsafe(32))
            return {**settings, "user": user, "password_hash": pw_hash, "salt": salt}
        pw_hash, salt = hash_password(password)
        creds = {**settings, "user": user, "password_hash": pw_hash, "salt": salt}
        save_creds(creds)
        return creds
    return {
        **settings,
        "user": admin["username"],
        "password_hash": admin["password_hash"],
        "salt": admin["salt"],
    }


def save_creds(creds):
    """Write the whole dict back: the login, and exactly these settings.

    A setting absent from `creds` is deleted, so `creds.pop(name)` then
    `save_creds(creds)` clears it, as it did when this was one JSON file.
    """
    settings = {k: v for k, v in creds.items() if k not in _LOGIN_FIELDS}
    with cloud_db.conn() as c:
        if all(f in creds for f in _LOGIN_FIELDS):
            c.execute(
                "INSERT INTO admin (id, username, password_hash, salt) "
                "VALUES (1, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                "username = EXCLUDED.username, password_hash = EXCLUDED.password_hash, "
                "salt = EXCLUDED.salt",
                (creds["user"], creds["password_hash"], creds["salt"]),
            )
        c.execute("DELETE FROM settings WHERE NOT (name = ANY(%s))", (list(settings),))
        for name, value in settings.items():
            c.execute(
                "INSERT INTO settings (name, value) VALUES (%s, %s) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value",
                (name, Jsonb(value)),
            )


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
_ADMIN_FAIL_MAX = 10
_ADMIN_FAIL_WINDOW = datetime.timedelta(minutes=15).total_seconds()
_admin_fails = {}  # ip -> [count, first_failure_monotonic]
_admin_fails_lock = threading.Lock()


def _admin_client_ip(request):
    """The caller's address. uvicorn rewrites this from X-Forwarded-For for the
    proxies named in FORWARDED_ALLOW_IPS (see docker-compose.yml), so behind Caddy
    it is the real client rather than the compose bridge."""
    return request.client.host if request.client else "?"


def _admin_locked(ip):
    with _admin_fails_lock:
        entry = _admin_fails.get(ip)
        if not entry:
            return False
        count, first = entry
        if time.monotonic() - first > _ADMIN_FAIL_WINDOW:
            del _admin_fails[ip]  # window elapsed — start clean
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
            for k in [
                k for k, v in _admin_fails.items() if now - v[1] > _ADMIN_FAIL_WINDOW
            ]:
                del _admin_fails[k]


def _admin_note_success(ip):
    with _admin_fails_lock:
        _admin_fails.pop(ip, None)


# ── Other admin users ──────────────────────────────────────────────────────────
# `admin` is everything, users and the infrastructure tabs included, and implies
# the rest; the others are one tab each.
CLOUD_ROLES = ("admin", "meets", "organizers", "appearance")


def clean_roles(roles):
    return [r for r in CLOUD_ROLES if r in set(roles or ())]


def expand_roles(roles):
    roles = set(clean_roles(roles))
    return set(CLOUD_ROLES) if "admin" in roles else roles


def load_users():
    """Every non-owner user, oldest first, without their password."""
    with cloud_db.conn() as c:
        rows = c.execute(
            "SELECT username, roles FROM admin_users ORDER BY created, username"
        ).fetchall()
    return [{"name": r["username"], "roles": clean_roles(r["roles"])} for r in rows]


def _user_row(name):
    with cloud_db.conn() as c:
        return c.execute(
            "SELECT * FROM admin_users WHERE username = %s", (name,)
        ).fetchone()


def user_exists(name):
    return name == load_creds()["user"] or _user_row(name) is not None


def add_user(name, password, roles):
    pw_hash, salt = hash_password(password)
    with cloud_db.conn() as c:
        c.execute(
            "INSERT INTO admin_users (username, password_hash, salt, roles) "
            "VALUES (%s, %s, %s, %s)",
            (name, pw_hash, salt, clean_roles(roles)),
        )


def update_user(name, password=None, roles=None):
    with cloud_db.conn() as c:
        if password:
            pw_hash, salt = hash_password(password)
            c.execute(
                "UPDATE admin_users SET password_hash = %s, salt = %s "
                "WHERE username = %s",
                (pw_hash, salt, name),
            )
        if roles is not None:
            c.execute(
                "UPDATE admin_users SET roles = %s WHERE username = %s",
                (clean_roles(roles), name),
            )


def delete_user(name):
    with cloud_db.conn() as c:
        c.execute("DELETE FROM admin_users WHERE username = %s", (name,))


def dump_users():
    """Every non-owner user with their password hash, for a full backup."""
    with cloud_db.conn() as c:
        rows = c.execute(
            "SELECT username, password_hash, salt, roles FROM admin_users "
            "ORDER BY created, username"
        ).fetchall()
    return [
        {
            "name": r["username"],
            "password_hash": r["password_hash"],
            "salt": r["salt"],
            "roles": clean_roles(r["roles"]),
        }
        for r in rows
    ]


def restore_users(users):
    """Upsert a full backup's users. Ones not in the backup are left alone."""
    with cloud_db.conn() as c:
        for u in users:
            if not isinstance(u, dict) or not u.get("name"):
                continue
            c.execute(
                "INSERT INTO admin_users (username, password_hash, salt, roles) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (username) DO UPDATE SET "
                "password_hash = EXCLUDED.password_hash, salt = EXCLUDED.salt, "
                "roles = EXCLUDED.roles",
                (
                    str(u["name"]),
                    str(u.get("password_hash", "")),
                    str(u.get("salt", "")),
                    clean_roles(u.get("roles")),
                ),
            )


def verify_user(name, password):
    """The tabs *name* may open when *password* is theirs, else None. One PBKDF2
    whichever way it goes, so the time taken does not say whether a name exists."""
    creds = load_creds()
    # compare_digest on the username too: `!=` returns on the first differing
    # character, which given enough attempts reveals it.
    if hmac.compare_digest(name, creds["user"]):
        stored, salt, roles = creds["password_hash"], creds["salt"], CLOUD_ROLES
    else:
        row = _user_row(name)
        if row is None:
            stored, salt, roles = "", "0" * 32, None
        else:
            stored, salt, roles = row["password_hash"], row["salt"], row["roles"]
    pw_hash, _ = hash_password(password, salt)
    if roles is None or not hmac.compare_digest(pw_hash, stored):
        return None
    return expand_roles(roles)


def authenticate(request):
    """`(name, roles)` for the request's Basic credentials, or None."""
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return None
    try:
        user, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
    except Exception:
        return None
    roles = verify_user(user, pw)
    return None if roles is None else (user, roles)


def check_admin(request):
    return authenticate(request) is not None


class require_role:
    """FastAPI dependency: a signed-in user who may open *role*'s tab, or any
    signed-in user when *role* is None. Leaves who it is in
    `request.state.admin_user` / `admin_roles` for the panel to read.

    A callable instance rather than a closure so `role` stays readable on it: the
    tests pin which route needs what.
    """

    def __init__(self, role: str | None):
        self.role = role

    def __call__(self, request: Request):
        _guard(request, self.role)


def _guard(request: Request, role):
    # Basic credentials are cached by the browser and sent on a cross-site form POST
    # too, so without this any page the admin visits could submit to /admin. Only
    # writes: a cross-site GET cannot read the reply, and a link to /admin from
    # elsewhere should still open. `Sec-Fetch-Site` is set by the browser and absent
    # on non-browser clients, which is why absence means allow.
    if (
        request.method not in ("GET", "HEAD")
        and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(status_code=403, detail="Cross-site request refused")
    ip = _admin_client_ip(request)
    if _admin_locked(ip):
        # 429, not 401: a browser answers 401 by re-prompting, which would walk the
        # operator into retrying against a lock that only their waiting clears.
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(int(_ADMIN_FAIL_WINDOW))},
        )
    who = authenticate(request)
    if who is None:
        _admin_note_failure(ip)
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Splouch Admin"'},
        )
    _admin_note_success(ip)
    # A test's stand-in request may have no `state`; a real one always does.
    if hasattr(request, "state"):
        request.state.admin_user, request.state.admin_roles = who
    if role is not None and role not in who[1]:
        raise HTTPException(status_code=403, detail="Not allowed for this user")


# Everything but the tabs other roles name: users, nodes, update, backup, debug.
require_admin = require_role("admin")
