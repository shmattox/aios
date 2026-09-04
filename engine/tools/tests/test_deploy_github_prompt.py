"""Sub-project A — the GitHub runner prompt mirrors the native run-frame and adds exactly three
deltas: no git, write result.json, no notifications. Paths are literal absolutes (env vars do not
survive across the agent's Bash calls)."""
import os, sys
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.normpath(os.path.join(_TOOLS, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "deploy", "github", "run-agent"))
import prompt as pr

ARGS = dict(leg="ingest", body_path="deploy/tasks/ingest.md", env_root="/w/env",
            plugin_root="/w/env/Projects/aios", result_path="/tmp/result.json", run_id="123")


def test_frame_names_roles_and_absolute_body_path():
    p = pr.build_prompt(**ARGS)
    assert "aios 'ingest' stage" in p
    assert "Env root: /w/env" in p and "Plugin root: /w/env/Projects/aios" in p
    assert "/w/env/Projects/aios/deploy/tasks/ingest.md" in p


def test_three_deltas_present_and_git_banned():
    p = pr.build_prompt(**ARGS)
    assert "Do NOT run any git command" in p
    assert '"status"' in p and "/tmp/result.json" in p and '"run_id": "123"' in p
    assert "Do NOT send notifications" in p


def test_cli_prints_prompt(capsys):
    rc = pr.main(["--leg", "ingest", "--body-path", "deploy/tasks/ingest.md", "--env-root", "/w/env",
                  "--plugin-root", "/w/env/Projects/aios", "--result-path", "/tmp/r.json", "--run-id", "9"])
    assert rc == 0
    assert "aios 'ingest' stage" in capsys.readouterr().out
