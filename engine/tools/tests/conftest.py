"""pytest collection config for the aios engine tool tests.

The `test_*.py` files in this directory are standalone scripts: each runs its checks at
import time and ends with `sys.exit(1 if FAIL else 0)`. pytest cannot import them directly
(the module-level `sys.exit` aborts collection with an INTERNALERROR). `suite_test.py` runs
each one as a subprocess and asserts it exits 0, so `python -m pytest tools/tests/ -q` is
green iff every script's checks pass. Tell pytest not to import the scripts itself.
"""
import glob
import os

_HERE = os.path.dirname(__file__)
collect_ignore = [os.path.basename(p) for p in glob.glob(os.path.join(_HERE, "test_*.py"))]
