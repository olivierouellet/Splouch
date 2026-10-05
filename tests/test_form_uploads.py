"""An uploaded file read off `request.form()` is recognised as one.

`request.form()` yields Starlette's `UploadFile`; FastAPI exports a subclass of it
under the same name. An `isinstance` check against FastAPI's never matches, so the
route took every file for absent and still answered `ok` — the cloud Appearance
tab's logo flashed its preview and vanished. Checked end to end on the logo, and by
import on every route that does the same check.
"""

import asyncio
import copy

import starlette.datastructures

# A 1×1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000000500010d0a2db40000000049454e44ae426082"
)


async def _call(app, method, path, body=b"", ctype=None):
    headers = [(b"host", b"t")]
    if ctype:
        headers.append((b"content-type", ctype.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("t", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }
    out = {"body": b""}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await app(scope, receive, send)
    return out


def _multipart(parts):
    out = b""
    for name, filename, ctype, data in parts:
        out += b"--B\r\n"
        if filename is None:
            out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        else:
            out += (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode()
        out += data + b"\r\n"
    return out + b"--B--\r\n", "multipart/form-data; boundary=B"


def test_an_uploaded_logo_is_stored_and_served(monkeypatch):
    import cloud_control

    store: dict = {}
    monkeypatch.setattr(cloud_control, "_load_creds", lambda: copy.deepcopy(store))
    monkeypatch.setattr(
        cloud_control, "_save_creds", lambda c: (store.clear(), store.update(c))
    )
    monkeypatch.setitem(
        cloud_control.app.dependency_overrides,
        cloud_control.require_admin,
        lambda: None,
    )
    # What the page posts: the whole form, the icon's file input left empty.
    body, ctype = _multipart(
        [
            ("picker_title", None, None, b"Splouch"),
            ("picker_window_title", None, None, b"Splouch"),
            ("picker_logo", "logo.png", "image/png", PNG),
            ("picker_icon", "", "application/octet-stream", b""),
        ]
    )

    async def go():
        post = await _call(
            cloud_control.app, "POST", "/admin/picker_appearance", body, ctype
        )
        get = await _call(cloud_control.app, "GET", "/picker_logo")
        return post, get

    post, get = asyncio.run(go())
    assert post["status"] == 200 and b'"ok":true' in post["body"]
    assert get["status"] == 200 and get["body"] == PNG


def test_every_isinstance_check_uses_starlette_s_upload_file():
    import cloud_control
    from routes import debug, system

    for mod in (cloud_control, system, debug):
        assert mod.UploadFile is starlette.datastructures.UploadFile, mod.__name__
