#!/usr/bin/env python3
"""url_extract.py — the inbox-capture step-2 "Extract rich" helper (A57).

Fetch a URL's full content as markdown via markitdown (the dataroom-ingest subprocess pattern) and
return it FAIL-SOFT: this function NEVER raises — it returns {"ok","markdown","reason"} for every
input, so a caller treats ok=False as "land the headline stub" and is never worse off than before.

Open web only (articles + YouTube transcripts). Auth-walled sources (x.com) return ok=False here —
they are handled by their own authed lanes, never by markitdown. Stdlib only in-process; markitdown
runs as a subprocess exactly as _tools/dataroom-ingest/convert.py does.
"""
import os, subprocess, sys

DEFAULT_TIMEOUT = 20
DEFAULT_MAX_BYTES = 5_000_000


# Truststore-bootstrap subprocess (live-verification findings, 2026-07-10):
#   (1) a bare `python -m markitdown <url>` dies with an SSL cert error behind this env's
#       TLS-inspection middlebox (the H44 issue) -> inject truststore first so Python trusts the OS
#       cert store. Best-effort (`try/except pass`) so it's a harmless no-op where there's no middlebox.
#   (2) markitdown's DEFAULT User-Agent gets a 403 from many article sites (Wikipedia, news) -> pass
#       a browser UA via a requests.Session, which recovers full-article extraction. Best-effort:
#       if `requests` is unavailable, fall back to a plain MarkItDown() rather than fail.
# markitdown/truststore-missing OR any fetch error -> the subprocess raises -> non-zero exit ->
# extract() returns ok=False (fail-soft), never worse than today.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BOOTSTRAP = (
    "import sys\n"
    "try:\n"
    "    import truststore; truststore.inject_into_ssl()\n"
    "except Exception:\n"
    "    pass\n"
    "from markitdown import MarkItDown\n"
    "try:\n"
    "    import requests\n"
    "    _s = requests.Session()\n"
    f"    _s.headers.update({{'User-Agent': {_UA!r}}})\n"
    "    _md = MarkItDown(requests_session=_s)\n"
    "except Exception:\n"
    "    _md = MarkItDown()\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "print(_md.convert(sys.argv[1]).text_content)\n"
)


def _default_run(url, timeout):
    """Real markitdown fetch via a truststore-bootstrap subprocess (see _BOOTSTRAP). PYTHONUTF8=1 so
    em-dashes don't become U+FFFD on Windows. Returns a CompletedProcess (returncode + stdout)."""
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-c", _BOOTSTRAP, url],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


def extract(url, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES, _run=None):
    """Fetch `url` -> {"ok": bool, "markdown": str, "reason": str}. NEVER raises. `_run` is the test
    seam (defaults to the real markitdown subprocess)."""
    run = _run or _default_run
    try:
        r = run(url, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "markdown": "", "reason": f"timeout>{timeout}s"}
    except Exception as e:
        return {"ok": False, "markdown": "", "reason": f"error:{type(e).__name__}"}
    rc = getattr(r, "returncode", 1)
    if rc != 0:
        return {"ok": False, "markdown": "", "reason": f"exit{rc}"}
    md = getattr(r, "stdout", "") or ""
    if not md.strip():
        return {"ok": False, "markdown": "", "reason": "empty"}
    if len(md.encode("utf-8", "replace")) > max_bytes:
        return {"ok": False, "markdown": "", "reason": "oversize"}
    return {"ok": True, "markdown": md, "reason": "ok"}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # a cp1252 console else crashes on rich markdown
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) < 2:
        print("usage: url_extract.py <url>"); sys.exit(1)
    res = extract(sys.argv[1])
    if res["ok"]:
        print(res["markdown"])
    else:
        print(f"(no rich extract: {res['reason']})"); sys.exit(2)
