"""`/help` — the pages this Pi serves, and who may open each.

One page per language, the language in the URL (`/aide` in French, `/ayuda` in
Spanish) rather than a cookie: it is the link an operator writes on a whiteboard
or reads out at the pool, and it has to open the same way on every phone. The
words are `[help]` in the panel files.
"""

from fastapi import APIRouter, Request

import state
from web import render

router = APIRouter(tags=["Help"])

# Path → language. A language added to `shared/locales/` without a word here
# still reads its page at `/help?lang=xx`.
HELP_PATHS = {"/help": "en", "/aide": "fr", "/ayuda": "es"}

# (group, path, role) — role None for a page open to everyone on the network. The
# roles are the ones each route requires (`require_role`, `require_login` being
# "admin"), so a page's lock here is the lock it has.
PAGES = (
    ("displays", "/live", None),
    ("displays", "/mobile", None),
    ("displays", "/results", None),
    ("displays", "/schedule", None),
    ("displays", "/full_schedule", None),
    ("displays", "/next_heats", None),
    ("displays", "/meet", None),
    ("operator", "/operator", None),
    ("operator", "/manual", "manual"),
    ("operator", "/console", "console"),
    ("operator", "/mm", "mm"),
    ("operator", "/settings", "admin"),
    ("account", "/login", None),
    ("account", "/logout", None),
    ("account", "/docs", "admin"),
)


def _help(request: Request, lang: str):
    installed = {c for c, _ in state.list_locales()}
    lang = request.query_params.get("lang", lang)
    lang = lang if lang in installed else "en"
    groups = {}
    for group, path, role in PAGES:
        groups.setdefault(group, []).append(
            {"path": path, "key": path.strip("/"), "role": role}
        )
    # Every language this server ships, each at its own path where it has one.
    by_lang = {code: path for path, code in HELP_PATHS.items()}
    return render(
        request,
        "help.html",
        lang=lang,
        t=state.help_strings(lang),
        groups=groups,
        languages=[
            (code, name, by_lang.get(code, f"/help?lang={code}"))
            for code, name in state.list_locales()
        ],
    )


@router.get("/help")
def route_help(request: Request):
    return _help(request, "en")


@router.get("/aide")
def route_aide(request: Request):
    return _help(request, "fr")


@router.get("/ayuda")
def route_ayuda(request: Request):
    return _help(request, "es")
