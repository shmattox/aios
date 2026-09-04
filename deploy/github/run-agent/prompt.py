"""Build the runner prompt for one leg on the GitHub Actions substrate (sub-project A).

Mirrors the native run-frame in deploy/windows/run-task.ps1 (role line, Env root, Plugin root,
"read your full instructions from"), then states the three substrate deltas. The native body is
executed VERBATIM; everything the substrate changes is here, nowhere else.
"""
import argparse
import posixpath
import sys

FRAME = (
    "You are the aios '{leg}' stage running UNATTENDED in GitHub Actions (headless, run {run_id}).\n"
    "Env root: {env_root}   Plugin root: {plugin_root}\n"
    "Read your full instructions from: {body}\n"
    "Execute them completely NOW. Do NOT ask questions or wait for input; follow the instructions "
    "exactly and finish. Substitute the literal absolute paths above into every command (env vars "
    "do not persist across separate Bash calls).\n"
    "\n"
    "GitHub-substrate deltas (these OVERRIDE the body wherever they conflict):\n"
    "1. Do NOT run any git command (no add/commit/push/pull/checkout/status). The workflow commits "
    "and pushes after you finish; any git instruction in the body is void here.\n"
    "2. When you finish — including when you fail — write the file {result_path} as JSON with "
    "exactly these keys: {{\"leg\": \"{leg}\", \"run_id\": \"{run_id}\", \"status\": "
    "\"ok\"|\"degraded\"|\"failed\", \"summary\": \"<the notification block the body tells you to "
    "render, as plain text>\", \"verify\": {{\"passed\": true|false, \"notes\": \"<what VERIFY "
    "checked>\"}}}}. \"status\" is \"failed\" when the body's VERIFY block found a mismatch or you "
    "could not complete the procedure; \"degraded\" when the run completed but reported a "
    "degradation; otherwise \"ok\". The file is the run's only success signal — never skip it.\n"
    "3. Do NOT send notifications and do NOT call any connector or MCP tool; the workflow renders "
    "the step summary from your result file.\n"
)


def build_prompt(leg, body_path, env_root, plugin_root, result_path, run_id):
    body = posixpath.join(plugin_root.replace("\\", "/"), body_path.replace("\\", "/"))
    return FRAME.format(leg=leg, run_id=run_id, env_root=env_root, plugin_root=plugin_root,
                        body=body, result_path=result_path)


def main(argv=None):
    p = argparse.ArgumentParser(description="aios GitHub-substrate runner prompt")
    for a in ("--leg", "--body-path", "--env-root", "--plugin-root", "--result-path", "--run-id"):
        p.add_argument(a, required=True)
    a = p.parse_args(argv)
    sys.stdout.write(build_prompt(a.leg, a.body_path, a.env_root, a.plugin_root,
                                  a.result_path, a.run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
