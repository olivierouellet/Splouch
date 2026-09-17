import subprocess
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, IPvAnyAddress
from starlette.concurrency import run_in_threadpool

import state
from web import ActionResult, EnabledFlag, render, require_login

router = APIRouter(tags=['Network'])


class EthIP(BaseModel):
    ip: IPvAnyAddress
    prefix: int = Field(24, ge=1, le=32)


class WifiConnect(BaseModel):
    ssid: str = ''
    password: str = ''


class WifiStatus(BaseModel):
    enabled: bool
    ssid: str
    wifi_ip: str
    eth_ip: str


class CloudStatus(BaseModel):
    connected: bool
    running: bool
    url: str
    # Attendance snapshot from the cloud: {'enabled': False} when analytics is
    # off there, else {'enabled': True, 'counts': {window: n}}. None until the
    # relay has heard back (or when disconnected).
    stats: dict | None = None




def _nmcli(*args, timeout=8):
    return subprocess.run(['nmcli'] + list(args),
                          capture_output=True, text=True, timeout=timeout)


def _split_terse(line, count):
    """Split an nmcli terse ('-t') line into `count` fields.

    nmcli escapes ':' and '\\' inside values, so we split on unescaped ':'
    only, then unescape. The last requested field absorbs any trailing
    unescaped ':' (there shouldn't be any given the field order)."""
    fields, buf, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == '\\' and i + 1 < len(line):
            buf.append(line[i + 1])
            i += 2
        elif c == ':' and len(fields) < count - 1:
            fields.append(''.join(buf))
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    fields.append(''.join(buf))
    while len(fields) < count:
        fields.append('')
    return fields


@router.get('/wifi_status', response_model=WifiStatus,
            dependencies=[Depends(require_login)])
def route_wifi_status():
    try:
        r       = _nmcli('radio', 'wifi')
        enabled = r.returncode == 0 and 'enabled' in r.stdout.lower()

        r2       = _nmcli('-t', '-f', 'DEVICE,TYPE', '--escape', 'no', 'device')
        wifi_dev = eth_dev = ''
        for line in r2.stdout.splitlines():
            parts = line.split(':')
            if len(parts) < 2:
                continue
            dev, typ = parts[0], parts[1]
            if typ == 'wifi' and not wifi_dev:
                wifi_dev = dev
            elif typ == 'ethernet' and not eth_dev:
                eth_dev = dev

        def get_ip(device):
            if not device:
                return ''
            r = _nmcli('-t', '-f', 'IP4.ADDRESS', '--escape', 'no', 'dev', 'show', device)
            for line in r.stdout.splitlines():
                if line.startswith('IP4.ADDRESS'):
                    return line.split(':')[-1].split('/')[0]
            return ''

        # IN-USE is the correct field for dev wifi (not ACTIVE); active AP is marked with '*'
        r3   = _nmcli('-t', '-f', 'IN-USE,SSID', '--escape', 'no', 'dev', 'wifi')
        ssid = ''
        for line in r3.stdout.splitlines():
            if line.startswith('*:'):
                ssid = line.split(':', 1)[1]
                break

        return {
            'enabled': enabled,
            'ssid':    ssid,
            'wifi_ip': get_ip(wifi_dev) if enabled else '',
            'eth_ip':  get_ip(eth_dev),
        }
    except FileNotFoundError:
        return JSONResponse({'error': 'nmcli not found'}, status_code=503)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.get('/wifi_scan', dependencies=[Depends(require_login)])
def route_wifi_scan(request: Request):
    # Returns an HTML fragment (HTMX hx-get) for the Network tab's list.
    try:
        # `dev wifi list` returns the *cached* scan immediately — right after
        # connecting that cache often holds only the associated AP, which is
        # why the list showed a single network. Trigger an explicit rescan and
        # give the driver a moment to report neighbours before listing.
        # rescan needs root: unprivileged callers get a polkit "not authorized"
        # error and the cache is never refreshed, so run it through sudo like
        # the other privileged nmcli calls here. It also exits non-zero if a
        # scan is already in progress — both cases are harmless, so ignore.
        subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'],
                       capture_output=True, text=True, timeout=20)
        time.sleep(4)
        # Terse (-t) output is one AP per line with ':'-separated fields, far
        # more robust than parsing the fixed-width table (whose column offsets
        # shift with content and silently drop most rows). SSID is requested
        # last because it can contain ':' — nmcli escapes those as '\:', which
        # _split_terse() honours so the SSID stays intact.
        r     = _nmcli('-t', '-f', 'IN-USE,SIGNAL,SECURITY,SSID',
                       'dev', 'wifi', 'list', timeout=20)
        # A single SSID can appear several times (one row per BSSID/band). Merge
        # them by SSID so each network shows once, keeping the strongest signal
        # and marking it active if *any* of its BSSIDs is the in-use one — the
        # connected AP is often not the strongest row, so dropping duplicates
        # naively would lose the '*' and hide the "Connected" state.
        by_ssid = {}
        for line in r.stdout.splitlines():
            if not line:
                continue
            active, signal, security, ssid = _split_terse(line, 4)
            if not ssid:
                continue
            sig = int(signal) if signal.isdigit() else 0
            net = by_ssid.get(ssid)
            if net is None:
                by_ssid[ssid] = {
                    'ssid':     ssid,
                    'signal':   sig,
                    'security': '' if security in ('--', '') else security,
                    'active':   '*' in active,
                }
            else:
                net['signal'] = max(net['signal'], sig)
                net['active'] = net['active'] or ('*' in active)
        networks = sorted(by_ssid.values(),
                          key=lambda n: n['signal'], reverse=True)
        return render(request, 'settings/fetched/wifi_networks.html', networks=networks)
    except FileNotFoundError:
        return render(request, 'settings/fetched/wifi_networks.html',
                      error='WiFi not available (nmcli not found).')
    except Exception as e:
        return render(request, 'settings/fetched/wifi_networks.html', error=str(e))


@router.post('/wifi_toggle', response_model=EnabledFlag,
             dependencies=[Depends(require_login)])
def route_wifi_toggle():
    try:
        r            = _nmcli('radio', 'wifi')
        currently_on = 'enabled' in r.stdout.lower()
        st           = 'off' if currently_on else 'on'
        subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', st],
                       capture_output=True, timeout=8)
        return {'enabled': not currently_on}
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.get('/cloud_status', response_model=CloudStatus,
            dependencies=[Depends(require_login)])
def route_cloud_status():
    import relay
    return relay.status()


@router.post('/cloud_toggle', response_model=CloudStatus,
             dependencies=[Depends(require_login)])
def route_cloud_toggle():
    import relay
    if relay.status()['running']:
        relay.stop()
    else:
        relay.start()
    return relay.status()


@router.post('/wifi_connect', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_wifi_connect(body: WifiConnect):
    # nmcli connect can take up to 30 s — run it off the event loop so live
    # scoreboard broadcasts keep flowing while it works.
    return await run_in_threadpool(_wifi_connect, body.ssid, body.password)


def _wifi_connect(ssid, password):
    if not ssid:
        return JSONResponse({'error': 'No SSID provided'}, status_code=400)
    try:
        # Remove any stale connection profile for this SSID first. nmcli
        # otherwise reuses an existing profile (e.g. from a previous attempt
        # on an open network) that lacks 802-11-wireless-security.key-mgmt,
        # which then fails validation once a password is supplied.
        # `--` first: an SSID is attacker-chosen text off the air, and one
        # beginning with `-` would otherwise be parsed as an nmcli option rather
        # than as the name of a connection.
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', '--', ssid],
                       capture_output=True, timeout=8)

        cmd = ['dev', 'wifi', 'connect', '--', ssid]
        if password:
            cmd += ['password', password]
        r = _nmcli(*cmd, timeout=30)
        if r.returncode == 0:
            return {'ok': True}
        return JSONResponse({'error': (r.stderr or r.stdout).strip()}, status_code=400)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post('/eth_dhcp_set', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_eth_dhcp_set():
    try:
        r = subprocess.run(
            ['sudo', 'nmcli', 'con', 'mod', 'splouch-eth',
             'ipv4.method', 'auto', 'ipv4.addresses', '', 'ipv4.gateway', ''],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return {'ok': False, 'error': r.stderr.strip() or 'nmcli error'}
        subprocess.run(['sudo', 'nmcli', 'con', 'up', 'splouch-eth'],
                       capture_output=True, timeout=8)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@router.post('/eth_ip_set', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_eth_ip_set(body: EthIP):
    # ip/prefix are already validated by the EthIP model.
    return await run_in_threadpool(_eth_ip_set, str(body.ip), body.prefix)


def _eth_ip_set(ip_str, prefix):
    cidr = f'{ip_str}/{prefix}'
    try:
        r = subprocess.run(
            ['sudo', 'nmcli', 'con', 'mod', 'splouch-eth', 'ipv4.addresses', cidr],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return {'ok': False, 'error': r.stderr.strip() or 'nmcli error'}
        subprocess.run(['sudo', 'nmcli', 'con', 'up', 'splouch-eth'],
                       capture_output=True, timeout=8)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@router.get('/clients_fragment', dependencies=[Depends(require_login)])
async def route_clients_fragment(request: Request):
    # Browser tabs connected to the scoreboard WebSocket, shown (HTMX hx-get) in
    # the Network tab. async so it runs on the event loop — the same context that
    # mutates _scoreboard_clients (the WS connect/disconnect handlers) — so this
    # snapshot can't be preempted mid-iteration by a connect/disconnect.
    return render(request, 'settings/fetched/clients.html',
                  clients=list(state._scoreboard_clients.values()),
                  server_version=state.git_describe()['version'],
                  t=state.settings_strings(state.ui_locale(request)))
