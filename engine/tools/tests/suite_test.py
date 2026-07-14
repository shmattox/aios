"""pytest entry point for the aios engine tool test suite.

The `test_*.py` files here are standalone scripts (they run their own checks at import and
`sys.exit` on failure), so pytest cannot collect them directly — `conftest.py` ignores them.
This file runs each as a subprocess and asserts exit 0, one parametrized pytest case per
script, so `python -m pytest tools/tests/ -q` exercises the whole suite. For granular,
per-check output run a script directly, e.g. `python tools/tests/test_capture.py`.
"""
import glob
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = sorted(os.path.basename(p) for p in glob.glob(os.path.join(_HERE, "test_*.py")))


@pytest.mark.parametrize("script", _SCRIPTS)
def test_script(script):
    """Run one standalone engine test script; fail (with its output) if it exits non-zero."""
    r = subprocess.run([sys.executable, os.path.join(_HERE, script)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")  # children emit emoji; piped Windows default is cp1252
    assert r.returncode == 0, (
        f"{script} exited {r.returncode}\n"
        f"--- stdout (tail) ---\n{r.stdout[-3000:]}\n"
        f"--- stderr (tail) ---\n{r.stderr[-1500:]}"
    )
