"""An uploaded file read off `request.form()` is recognised as one.

`request.form()` yields Starlette's `UploadFile`; FastAPI exports a subclass of it
under the same name. An `isinstance` check against FastAPI's never matches, so the
route took every file for absent and still answered `ok` — the cloud Appearance
tab's logo flashed its preview and vanished. Checked end to end on the logo, and by
import on every route that does the same check.
"""

import asyncio
import copy
import io

import starlette.datastructures
from PIL import Image

# A 1×1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000000500010d0a2db40000000049454e44ae426082"
)


async def _call(app, method, path, body=b"", ctype=None, accept=None):
    headers = [(b"host", b"t")]
    if accept:
        headers.append((b"accept", accept.encode()))
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


# Chrome's Accept for an `<img>`; the apps' loaders send `*/*`.
BROWSER_IMG = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def _logo_store(monkeypatch):
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
    return store


def _upload_logo(filename, ctype, data, accepts):
    import cloud_control

    body, form = _multipart([("picker_logo", filename, ctype, data)])

    async def go():
        post = await _call(
            cloud_control.app, "POST", "/admin/picker_appearance", body, form
        )
        gets = [
            await _call(cloud_control.app, "GET", "/picker_logo", accept=a)
            for a in accepts
        ]
        return post, gets

    return asyncio.run(go())


def test_an_svg_logo_reaches_the_apps_as_png(monkeypatch):
    """iOS's UIImage and Android's BitmapFactory decode no SVG; browsers do."""
    _logo_store(monkeypatch)
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 50">'
        b'<rect width="200" height="50" fill="#00f"/></svg>'
    )
    post, (web, app) = _upload_logo(
        "logo.svg", "image/svg+xml", svg, [BROWSER_IMG, "*/*"]
    )
    assert b'"ok":true' in post["body"]
    assert web["body"] == svg
    png = Image.open(io.BytesIO(app["body"]))
    assert png.format == "PNG" and png.size == (1024, 256)
    assert png.convert("RGB").getpixel((512, 128)) == (0, 0, 255)


def test_a_tall_svg_logo_is_capped_by_its_height(monkeypatch):
    _logo_store(monkeypatch)
    # Millimetres, as print tools export: resvg refuses them without a DPI.
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="40mm">'
        b'<rect width="5" height="5"/></svg>'
    )
    _, (app,) = _upload_logo("logo.svg", "image/svg+xml", svg, ["*/*"])
    w, h = Image.open(io.BytesIO(app["body"])).size
    assert h == 1024 and abs(w - 256) <= 2


def test_the_png_copy_draws_no_file_off_this_server(monkeypatch, tmp_path):
    """A browser's `<img>` loads no external reference; resvg would read any path."""
    _logo_store(monkeypatch)
    secret = tmp_path / "secret.png"
    Image.new("RGB", (4, 4), "red").save(secret)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">'
        f'<image href="{secret}" width="50" height="100"/>'
        f'<image xlink:href="file://{secret}" x="50" width="50" height="100"/>'
        "</svg>"
    ).encode()
    _, (app,) = _upload_logo("logo.svg", "image/svg+xml", svg, ["*/*"])
    png = Image.open(io.BytesIO(app["body"])).convert("RGBA")
    assert png.getpixel((256, 512))[3] == 0 and png.getpixel((768, 512))[3] == 0


def test_an_unreadable_svg_is_refused(monkeypatch):
    store = _logo_store(monkeypatch)
    post, _ = _upload_logo("logo.svg", "image/svg+xml", b"<svg", [])
    assert b'"ok":false' in post["body"] and "picker_logo_b64" not in store


def test_a_raster_logo_drops_the_old_png_copy(monkeypatch):
    store = _logo_store(monkeypatch)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    _upload_logo("logo.svg", "image/svg+xml", svg, [])
    assert store["picker_logo_png_b64"]
    _, (app,) = _upload_logo("logo.png", "image/png", PNG, ["*/*"])
    assert "picker_logo_png_b64" not in store and app["body"] == PNG


def test_every_isinstance_check_uses_starlette_s_upload_file():
    import cloud_control
    from routes import debug, system

    for mod in (cloud_control, system, debug):
        assert mod.UploadFile is starlette.datastructures.UploadFile, mod.__name__
