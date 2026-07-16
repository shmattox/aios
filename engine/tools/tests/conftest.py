"""pytest collection config for the aios engine tool tests.

Two kinds of `test_*.py` file live here:

- **Legacy scripts** — they run their checks at import time and end with an UNGUARDED
  `sys.exit(1 if FAIL else 0)`. pytest cannot import these (the module-level `sys.exit`
  aborts collection with an INTERNALERROR), so they are ignored here; `suite_test.py`
  subprocesses each and asserts exit 0.
- **Files defining pytest tests** (`def test_*` at module level) — these are import-safe by
  construction (either pure pytest modules, or hybrids whose legacy `sys.exit` is wrapped in
  an `if __name__ == "__main__"` guard precisely so pytest can import them). pytest MUST
  collect these or their tests do not run at all.

The ignore list used to be a blanket glob over `test_*.py`, which silently dropped ~415
pytest tests from `python -m pytest -q` — the repo's own configured test command. Four files
(`test_brief_threads`, `test_gate_metrics`, `test_reflect`, `test_url_extract`) are pure
pytest modules with no module-level work at all, so `suite_test.py`'s subprocess run of them
was a vacuous exit-0 and pytest was ignoring them: their tests ran NOWHERE. A canary
(`assert False` in a pytest def) proved `python -m pytest -q` stayed green. Ignore only what
pytest genuinely cannot import.

Note the two mechanisms are complementary, not redundant: a hybrid file's module-level
`check()` block only *reports* failures via its FAIL list, and only the standalone run turns
that into a non-zero exit — so `suite_test.py` must keep subprocessing hybrids even though
pytest also collects them for their `def test_*`.
"""
import glob
import os
import re

_HERE = os.path.dirname(__file__)

# A module-level `def test_*` means the file is meant for pytest collection.
_PYTEST_DEF = re.compile(r"^def test_", re.MULTILINE)


def _defines_pytest_tests(path):
    try:
        with open(path, encoding="utf-8") as f:
            return bool(_PYTEST_DEF.search(f.read()))
    except OSError:
        return False


collect_ignore = [
    os.path.basename(p)
    for p in glob.glob(os.path.join(_HERE, "test_*.py"))
    if not _defines_pytest_tests(p)
]
