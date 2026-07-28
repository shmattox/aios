"""A109 task 6 — UI shell static wiring (no-build invariants)."""
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "dashboard" / "ui"


def test_vendored_esm_present_and_relative():
    for name in ("preact.mjs", "hooks.mjs", "htm.mjs"):
        text = (UI / "vendor" / name).read_text(encoding="utf-8")
        # no absolute (/v135/...) or bare-specifier imports survive — must resolve offline
        assert 'from"/' not in text and "from '/v" not in text, f"{name}: absolute import survives"
        assert 'from"preact"' not in text and "from 'preact'" not in text, f"{name}: bare import survives"


def test_index_has_viewport_and_token():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in html          # v1 gap, mockup-found — required
    assert "{{TOKEN}}" in html                 # server injection contract unchanged
    assert 'id="root"' in html                 # Preact mount point


def test_no_node_artifacts():
    root = UI.parents[2]
    assert not (root / "package.json").exists()
    assert not (root / "node_modules").exists()
