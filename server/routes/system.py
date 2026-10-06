import datetime
import io
import os
import re
import subprocess
import tarfile
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile  # what request.form() yields

import bus
import paths
import state
from web import ActionResult, LogTail, failure, require_login

router = APIRouter(tags=["System"])


class TimeSet(BaseModel):
    # The pattern is advertised in the schema for /docs; enforcement is done in
    # the validator so a bad value yields the app's friendly single-line error
    # rather than Pydantic's regex-echoing default.
    date: str = Field(json_schema_extra={"pattern": r"^\d{4}-\d{2}-\d{2}$"})
    time: str = Field(json_schema_extra={"pattern": r"^\d{2}:\d{2}(:\d{2})?$"})

    @field_validator("date")
    @classmethod
    def _check_date(cls, v):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("Invalid date or time format")
        return v

    @field_validator("time")
    @classmethod
    def _check_time(cls, v):
        if not re.match(r"^\d{2}:\d{2}(:\d{2})?$", v):
            raise ValueError("Invalid date or time format")
        return v


class TimeStatus(BaseModel):
    date: str
    time: str
    timezone: str
    ntp_active: bool
    synchronized: bool


class RtcStatus(BaseModel):
    configured: bool
    active: bool


def run_cmd_blocking(cmd, cwd=None):
    """Run *cmd*, returning (combined output, exit code). Blocking.

    Public and up here because two unrelated panels drive long jobs through it —
    the updater in `routes/update.py` and the RTC installer below — and neither
    owns it.
    """
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.stdout, proc.returncode


# ── Time ───────────────────────────────────────────────────────────────────────


@router.get(
    "/time_status", response_model=TimeStatus, dependencies=[Depends(require_login)]
)
def route_time_status():
    now = datetime.datetime.now()
    ntp_active = False
    synchronized = False
    timezone = ""
    try:
        result = subprocess.run(
            ["timedatectl"], capture_output=True, text=True, timeout=5, check=False
        )
        for line in result.stdout.splitlines():
            ll = line.lower()
            if "ntp service" in ll:
                ntp_active = "active" in ll
            if "system clock synchronized" in ll:
                synchronized = "yes" in ll
            if "time zone" in ll:
                m = re.search(r"Time zone:\s+\S+\s+\((\w+)", line)
                if m:
                    timezone = m.group(1)
    except Exception:
        pass
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone,
        "ntp_active": ntp_active,
        "synchronized": synchronized,
    }


@router.post(
    "/time_sync", response_model=ActionResult, dependencies=[Depends(require_login)]
)
def route_time_sync():
    try:
        for cmd in (
            ["sudo", "timedatectl", "set-ntp", "true"],
            ["sudo", "systemctl", "restart", "systemd-timesyncd"],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, check=False
            )
            if r.returncode != 0:
                return {"ok": False, "error": r.stderr.strip() or f"{cmd[1]} failed"}
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": failure("Syncing the time")}


@router.post(
    "/time_set", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_time_set(body: TimeSet):
    return await run_in_threadpool(_time_set, body.date, body.time)


def _time_set(date_str, time_str):
    # date/time formats are already validated by the TimeSet model.
    if len(time_str) == 5:
        time_str += ":00"
    try:
        subprocess.run(
            ["sudo", "timedatectl", "set-ntp", "false"], timeout=5, check=True
        )
        subprocess.run(
            ["sudo", "timedatectl", "set-time", f"{date_str} {time_str}"],
            timeout=5,
            check=True,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── RTC (Adafruit PiRTC DS3231) ──────────────────────────────────────────────────


@router.get(
    "/rtc_status", response_model=RtcStatus, dependencies=[Depends(require_login)]
)
def route_rtc_status():
    configured = False
    active = False

    for config_txt in ("/boot/firmware/config.txt", "/boot/config.txt"):
        try:
            with open(config_txt, encoding="utf-8") as f:
                if any(line.strip() == "dtoverlay=i2c-rtc,ds3231" for line in f):
                    configured = True
                    break
        except OSError:
            continue

    try:
        with open("/sys/class/rtc/rtc0/name", encoding="utf-8") as f:
            name = f.read().lower()
        if "ds3231" in name or "rtc-ds1307" in name:
            active = True
    except OSError:
        pass

    return {"configured": configured, "active": active}


def _run_rtc(action):
    state._rtc_log_lines = []
    state._rtc_log_done = None

    def emit(text, error=False):
        state._rtc_log_lines.append({"text": text, "error": error})

    try:
        # Run directly, not through `bash`: the sudo rule names the script itself,
        # and `sudo bash <script>` matched no rule at all.
        script = paths.privileged_script("rtc_setup.sh")
        emit(f"$ sudo {script} {action}\n")
        out, rc = run_cmd_blocking(["sudo", "-n", script, action])
        if out:
            emit(out)
        if rc != 0:
            emit(f"\nCommand failed (exit code {rc})\n", error=True)
            state._rtc_log_done = False
            return
        state._rtc_log_done = True
    except Exception as e:
        emit(f"\nError: {e}\n", error=True)
        state._rtc_log_done = False
    finally:
        state._rtc_in_progress = False


@router.post(
    "/rtc_install_start",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_rtc_install_start():
    if state._rtc_in_progress:
        return JSONResponse({"error": "RTC setup already in progress"}, status_code=409)
    state._rtc_in_progress = True
    bus.run_bg(_run_rtc, "enable")
    return {"ok": True}


@router.post(
    "/rtc_remove_start",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_rtc_remove_start():
    if state._rtc_in_progress:
        return JSONResponse({"error": "RTC setup already in progress"}, status_code=409)
    state._rtc_in_progress = True
    bus.run_bg(_run_rtc, "disable")
    return {"ok": True}


@router.get("/rtc_log", response_model=LogTail, dependencies=[Depends(require_login)])
def route_rtc_log():
    return {"lines": state._rtc_log_lines, "done": state._rtc_log_done}


@router.get("/logs_download", dependencies=[Depends(require_login)])
def route_logs_download():
    text = "\n".join(state._log_ring) + "\n"
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return Response(
        content=text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="splouch-log-{ts}.log"'},
    )


@router.post("/logs_save", dependencies=[Depends(require_login)])
def route_logs_save():
    try:
        os.makedirs(state.LOGS_DIR, exist_ok=True)
        name = (
            "splouch-log-"
            + datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            + ".log"
        )
        path = os.path.join(state.LOGS_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(state._log_ring) + "\n")
        return {"ok": True, "path": path}
    except Exception:
        return JSONResponse(
            {"ok": False, "error": failure("Saving the log")}, status_code=500
        )


@router.post(
    "/system_reboot", response_model=ActionResult, dependencies=[Depends(require_login)]
)
def route_system_reboot():
    def _reboot():
        time.sleep(1)
        subprocess.run(["sudo", "reboot"], check=False)

    bus.run_bg(_reboot)
    return {"ok": True}


@router.post(
    "/system_shutdown",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_system_shutdown():
    def _shutdown():
        time.sleep(1)
        subprocess.run(["sudo", "poweroff"], check=False)

    bus.run_bg(_shutdown)
    return {"ok": True}


@router.post(
    "/system_service_restart",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_service_restart():
    # Restart just the app (splouch.service) — far faster than a full reboot,
    # and the same command the updater runs. Deferred so this response returns
    # before systemd kills the process serving it.
    def _restart():
        time.sleep(1)
        subprocess.run(
            ["sudo", "systemctl", "restart", state.SERVICE_NAME], check=False
        )

    bus.run_bg(_restart)
    return {"ok": True}


# The archive's root folder: the data folder's own name. `Splouch` is what the
# download called it before, and is still accepted on restore — it held the same
# files, it was only the name that pointed at the checkout.
_BACKUP_ROOT = "SplouchData"
_LEGACY_BACKUP_ROOTS = ("Splouch",)

# Not carried between installs: the session key is what makes one Pi's cookies
# worthless on another (paths.session_secret), and a restored copy would make two
# Pis accept each other's sessions. A restore without it simply signs everyone out.
_NOT_BACKED_UP = (".session_key",)


def _backup_member(info):
    if os.path.basename(info.name) in _NOT_BACKED_UP:
        return None
    return info


def _restorable(tar):
    """The archive's members under its root folder, renamed relative to it."""
    members = []
    for m in tar.getmembers():
        top, _, rest = m.name.removeprefix("./").partition("/")
        if top not in (_BACKUP_ROOT, *_LEGACY_BACKUP_ROOTS) or not rest:
            continue
        if os.path.basename(rest) in _NOT_BACKED_UP:
            continue
        m.name = rest
        if m.islnk():  # a hard link names its target from the archive root too
            m.linkname = m.linkname.removeprefix("./").partition("/")[2]
        members.append(m)
    return members


@router.get("/backup_download", dependencies=[Depends(require_login)])
def route_backup_download():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(state.SCOREBOARD_DIR, arcname=_BACKUP_ROOT, filter=_backup_member)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="splouch_backup_{date_str}.tar.gz"'
        },
    )


@router.post(
    "/backup_restore",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_backup_restore(request: Request):
    form = await request.form()
    f = form.get("backup_file")
    if not isinstance(f, UploadFile) or not (f.filename or "").endswith(".tar.gz"):
        return JSONResponse(
            {"ok": False, "error": "Please upload a .tar.gz backup file"},
            status_code=400,
        )
    data = await f.read()
    # Extracting the tarball is blocking; run it off the event loop.
    return await run_in_threadpool(_restore_backup, data)


def _restore_backup(data):
    try:
        buf = io.BytesIO(data)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            members = _restorable(tar)
            if not members:
                return JSONResponse(
                    {"ok": False, "error": "Not a Splouch backup"}, status_code=400
                )
            # Into the data folder, and only what was under the archive's root
            # folder. This extracted into `~` once, as whatever paths the archive
            # named: the download's root was `Splouch/`, which is the *checkout*,
            # so a restore never put the data back — and an archive someone handed
            # the operator could replace the server's own code.
            #
            # `filter='data'` is what contains the extraction beyond that: it
            # refuses absolute and `..` paths, links pointing outside the
            # destination, and device/setuid entries.
            tar.extractall(path=state.SCOREBOARD_DIR, members=members, filter="data")
    except tarfile.TarError:
        # Not a gzipped tar, or an entry the `data` filter refused.
        return JSONResponse(
            {"ok": False, "error": "Not a valid backup archive"}, status_code=400
        )
    except Exception:
        return JSONResponse(
            {"ok": False, "error": failure("Restoring the backup")}, status_code=500
        )

    # Restart after a short delay so the response can be sent first
    def _restart():
        time.sleep(1)
        subprocess.run(
            ["sudo", "systemctl", "restart", state.SERVICE_NAME], check=False
        )

    bus.run_bg(_restart)
    return {"ok": True}
