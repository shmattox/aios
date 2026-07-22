#!/usr/bin/env python3
"""runtime_fingerprint.py test harness (A90) — the four drift statuses, engine-scoped sha (docs-only
commit stays clean), fail-soft on a malformed install record, sibling dev-clone resolution, and the
drift line flowing through the brief's delta-gated health filter. Scratch; safe to delete."""
import json, os, sys, tempfile, shutil, subprocess

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../engine/tools
sys.path.insert(0, HARNESS)
import runtime_fingerprint as rf
import brief_render

# isolate from a real dev clone on the running machine — _find_repo_root honors $AIOS_DEV_CLONE, which
# would otherwise resolve ahead of the temp fixtures and make cases #5/#7 environment-dependent.
os.environ.pop("AIOS_DEV_CLONE", None)

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else " FAIL ") + name)

def git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=env)

d = tempfile.mkdtemp(prefix="rf_")
try:
    repo = os.path.join(d, "aios")
    os.makedirs(os.path.join(repo, ".claude-plugin"))
    os.makedirs(os.path.join(repo, "engine"))
    json.dump({"name": "aios", "version": "0.6.2"},
              open(os.path.join(repo, ".claude-plugin", "plugin.json"), "w"))
    open(os.path.join(repo, "engine", "tool.py"), "w").write("x = 1\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "engine init")
    engine_sha = git(repo, "log", "-1", "--format=%H", "--", "engine/").stdout.strip()

    n = [0]
    def installed(v, sha, scope="user", key="aios@aios", malformed=False):
        n[0] += 1
        p = os.path.join(d, f"installed_{n[0]}.json")
        if malformed:
            open(p, "w").write("{ not json ")
        else:
            json.dump({"version": 2, "plugins": {key: [
                {"scope": scope, "version": v, "gitCommitSha": sha}]}}, open(p, "w"))
        return p

    # 1. clean — matching version + engine sha
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", engine_sha))
    check("clean: matching version + engine sha", fp["status"] == "clean" and rf.render(fp) == "")

    # 2. stale-version
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.1", engine_sha))
    check("stale-version: version behind (plugin update)",
          fp["status"] == "stale-version" and "plugin update" in fp["message"])
    check("stale-version: render emits one drift line", rf.render(fp).startswith("⚠️ Engine drift"))

    # 3. stale-sha — same version, sha diverged (the 2026-07-14 incident version-compare misses)
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", "d" * 40))
    check("stale-sha: same version, engine sha diverged",
          fp["status"] == "stale-sha" and "without a version bump" in fp["message"])

    # 4. sha scoped to engine/: a docs-only commit does NOT flip clean → stale-sha
    os.makedirs(os.path.join(repo, "docs"), exist_ok=True)
    open(os.path.join(repo, "docs", "note.md"), "w").write("doc\n")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "docs only")
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", engine_sha))
    check("sha scoped to engine/: docs-only commit stays clean",
          fp["status"] == "clean" and fp["repo"]["sha"] == engine_sha)

    # 4b. install at a HEAD past the engine sha (the bump-then-reinstall flow): a version-bump or
    # docs commit advances HEAD, the install records that HEAD, and the engine sha is an ANCESTOR
    # of it — the install contains every engine change, so this must be clean, not stale-sha.
    head_sha = git(repo, "log", "-1", "--format=%H").stdout.strip()
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", head_sha))
    check("ancestry: install at HEAD past the engine sha stays clean (bump-then-reinstall)",
          fp["status"] == "clean")

    # 5. no-dev-clone — unresolvable repo degrades silent
    fp = rf.fingerprint(repo_root=os.path.join(d, "nope"),
                        installed_plugins_path=installed("0.6.2", engine_sha),
                        env_root=os.path.join(d, "nostate"))
    check("no-dev-clone: unresolvable repo degrades silent",
          fp["status"] == "no-dev-clone" and rf.render(fp) == "")

    # 6. fail-soft — malformed installed_plugins.json -> no-dev-clone, never raises
    fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", "x", malformed=True))
    check("fail-soft: malformed installed_plugins.json -> no-dev-clone, no raise",
          fp["status"] == "no-dev-clone")

    # 7. env-root sibling resolution — finds <env_root>/Projects/aios
    envroot = os.path.join(d, "envroot")
    sib = os.path.join(envroot, "Projects", "aios", ".claude-plugin")
    os.makedirs(sib)
    json.dump({"name": "aios", "version": "0.6.2"}, open(os.path.join(sib, "plugin.json"), "w"))
    fp = rf.fingerprint(installed_plugins_path=installed("0.6.2", engine_sha), env_root=envroot)
    check("env-root resolution: finds sibling Projects/aios clone", fp["status"] != "no-dev-clone")

    # 8. CLI — exit 0 always; --json prints the dict; --line prints the drift line
    r = subprocess.run([sys.executable, os.path.join(HARNESS, "runtime_fingerprint.py"), "--json",
                        "--repo-root", repo, "--installed-plugins", installed("0.6.2", engine_sha)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("CLI: --json exits 0 and prints status", r.returncode == 0 and '"status"' in r.stdout)
    r2 = subprocess.run([sys.executable, os.path.join(HARNESS, "runtime_fingerprint.py"), "--line",
                         "--repo-root", repo, "--installed-plugins", installed("0.6.1", engine_sha)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("CLI: --line exits 0 and prints the drift line for a stale install",
          r2.returncode == 0 and "Engine drift" in r2.stdout)

    # 9. brief render — a staled install surfaces exactly one drift line; a clean one none
    stale_fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.1", engine_sha))
    lines = {"runtime_drift": rf.render(stale_fp)} if rf.render(stale_fp) else {}
    shown, _ = brief_render.filter_health_lines(lines, {})
    check("brief: a staled install surfaces exactly one drift health line",
          shown.get("runtime_drift", "").startswith("⚠️ Engine drift"))
    clean_fp = rf.fingerprint(repo_root=repo, installed_plugins_path=installed("0.6.2", engine_sha))
    check("brief: a clean install renders no drift line", rf.render(clean_fp) == "")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
finally:
    shutil.rmtree(d, ignore_errors=True)
