#!/usr/bin/env python3
"""Hermetic tests for url_extract — the subprocess is mocked; NO network."""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import url_extract as ux

class _R:                      # a fake CompletedProcess
    def __init__(self, returncode, stdout): self.returncode, self.stdout = returncode, stdout

def _run_ok(url, timeout):     return _R(0, "# Title\n\nreal body content\n")
def _run_empty(url, timeout):  return _R(0, "   \n")
def _run_fail(url, timeout):   return _R(1, "")
def _run_timeout(url, timeout): raise subprocess.TimeoutExpired(cmd="markitdown", timeout=timeout)
def _run_boom(url, timeout):   raise OSError("spawn failed")

def test_success_returns_ok_and_markdown():
    r = ux.extract("http://x", _run=_run_ok)
    assert r["ok"] is True and "real body content" in r["markdown"] and r["reason"] == "ok"

def test_empty_output_is_not_ok():
    r = ux.extract("http://x", _run=_run_empty)
    assert r["ok"] is False and r["reason"] == "empty" and r["markdown"] == ""

def test_nonzero_exit_is_not_ok():
    r = ux.extract("http://x", _run=_run_fail)
    assert r["ok"] is False and r["reason"].startswith("exit")

def test_timeout_is_not_ok_and_never_raises():
    r = ux.extract("http://x", timeout=5, _run=_run_timeout)
    assert r["ok"] is False and "timeout" in r["reason"]

def test_arbitrary_exception_is_swallowed():
    r = ux.extract("http://x", _run=_run_boom)
    assert r["ok"] is False and r["reason"].startswith("error:")

def test_oversize_output_is_not_ok():
    big = "x" * 20
    r = ux.extract("http://x", max_bytes=5, _run=lambda u, t: _R(0, big))
    assert r["ok"] is False and r["reason"] == "oversize"

def test_malformed_run_result_never_raises():
    # a _run returning an object lacking returncode/stdout must NOT raise (the cardinal never-raise
    # invariant the whole fail-soft design rests on)
    class _Bare: pass
    r = ux.extract("http://x", _run=lambda u, t: _Bare())
    assert r["ok"] is False and r["reason"].startswith("exit")
