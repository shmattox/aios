"""D §3.2 — the Host allowlist is a longer EXACT-MATCH set, never a hole.

`_host_ok` is the DNS-rebinding defence (dashboard_server.py module header: "127.0.0.1 bind, exact
Host validation, per-start token on every POST"). `tailscale serve` proxies with the tailnet
hostname, so reach requires widening it — and widening it wrongly deletes the defence.

Every test here defends one of two properties: the set stays exact, and the loopback bind stays.
They are asserted TOGETHER because they are one security decision in two places — widening Host is
safe only while nothing binds 0.0.0.0.
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import make_server  # noqa: E402


def _env(tmp_path, extra_hosts_yaml=""):
    prof = tmp_path / "profile"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "connectors.yaml").write_text(
        "vault:\n  live_root: \"SecondBrain\"\n  live_kb_map:\n    personal: \"01_Personal\"\n"
        + extra_hosts_yaml, encoding="utf-8")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _serve(env_root):
    srv = make_server(env_root, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _get(srv, host_header):
    port = srv.server_address[1]
    req = urllib.request.Request("http://127.0.0.1:%d/api/health" % port)
    req.add_header("Host", host_header)
    return urllib.request.urlopen(req, timeout=10)


def test_default_is_unchanged_loopback_only(tmp_path):
    """An install that configures nothing must behave exactly as before."""
    srv = _serve(_env(tmp_path))
    try:
        port = srv.server_address[1]
        with _get(srv, "127.0.0.1:%d" % port) as r:
            assert r.status == 200
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, "desktop.tail1234.ts.net:%d" % port)
        assert e.value.code == 403, "an unconfigured host must still be refused"
    finally:
        srv.shutdown()


def test_configured_host_is_accepted_and_others_still_403(tmp_path):
    port_placeholder = "desktop.tail1234.ts.net"
    srv = _serve(_env(tmp_path,
                      "dashboard:\n  extra_hosts:\n    - \"%s\"\n" % port_placeholder))
    try:
        port = srv.server_address[1]
        with _get(srv, "%s:%d" % (port_placeholder, port)) as r:
            assert r.status == 200, "the configured tailnet host must be allowed"
        # the set is EXACT: a neighbouring name on the same tailnet is not implied
        for bad in ("evil.tail1234.ts.net", "desktop.tail1234.ts.net.evil.com",
                    "adesktop.tail1234.ts.net"):
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(srv, "%s:%d" % (bad, port))
            assert e.value.code == 403, "%s must be refused — the allowlist is exact" % bad
    finally:
        srv.shutdown()


def test_a_wildcard_entry_is_refused_at_startup(tmp_path):
    """The failure mode §7 names: three characters of config that delete the defence."""
    for i, bad in enumerate(("*", "*.ts.net", "")):
        env = _env(tmp_path / ("case%d" % i),
                   "dashboard:\n  extra_hosts:\n    - \"%s\"\n" % bad)
        with pytest.raises(ValueError) as e:
            make_server(env, port=0)
        assert "wildcard" in str(e.value).lower() or "empty" in str(e.value).lower(), e.value


def test_the_bind_is_still_loopback(tmp_path):
    """Asserted in the SAME file as the allowlist: widening Host is safe only while the bind is
    loopback. If this ever fails, the allowlist has become real exposure."""
    srv = _serve(_env(tmp_path, "dashboard:\n  extra_hosts:\n    - \"desktop.tail1234.ts.net\"\n"))
    try:
        assert srv.server_address[0] == "127.0.0.1", \
            "the server must bind loopback; tailscale serve is the only path in"
    finally:
        srv.shutdown()


def test_bare_hostname_matches_the_default_port_form_a_browser_sends(tmp_path):
    """Review finding 1. `tailscale serve` terminates TLS on 443 and a browser OMITS the default
    port, so the real Host header is a bare hostname. Appending the backend port ONLY would have
    403'd the exact deployment this feature exists for."""
    host = "desktop.tail1234.ts.net"
    srv = _serve(_env(tmp_path, "dashboard:\n  extra_hosts:\n    - \"%s\"\n" % host))
    try:
        with _get(srv, host) as r:                       # no port — the 443 case
            assert r.status == 200
        port = srv.server_address[1]
        with _get(srv, "%s:%d" % (host, port)) as r:     # direct-to-port case
            assert r.status == 200
    finally:
        srv.shutdown()


def test_non_mapping_dashboard_section_refuses_to_start(tmp_path):
    """Review finding 2: was an uncaught AttributeError; now a stated refusal."""
    env = _env(tmp_path, "dashboard: oops\n")
    with pytest.raises(ValueError) as e:
        make_server(env, port=0)
    assert "mapping" in str(e.value)


def test_case_variants_are_not_accepted(tmp_path):
    """Nothing normalises case today, so this is a false negative rather than a hole — pinned so a
    future 'helpful' .lower() cannot quietly widen the set."""
    host = "desktop.tail1234.ts.net"
    srv = _serve(_env(tmp_path, "dashboard:\n  extra_hosts:\n    - \"%s\"\n" % host))
    try:
        for variant in (host.upper(), "Desktop.Tail1234.TS.net", host + "."):
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(srv, variant)
            assert e.value.code == 403, "%s must not be accepted" % variant
    finally:
        srv.shutdown()


def test_missing_connectors_file_fails_closed(tmp_path):
    """No profile at all -> loopback only, never an open set."""
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    srv = make_server(tmp_path, port=0)
    try:
        assert srv.extra_hosts == frozenset()
    finally:
        srv.server_close()
