# Testing AIOS — a quick install run

Thanks for trying this out. AIOS is a Claude Code plugin — a personal knowledge
system that captures items, drafts them into your notes, and asks you to
approve anything sensitive before it ships. It's brand new, and you're the
first person outside its author to install it. The goal of this test is
simple: **find where it stumbles.**

## What you need

- Claude Code installed and working
- Python 3 on your machine
- git installed
- Notion is optional — the setup will ask if you want to connect it, and it
  works fine if you say no

You don't need an existing vault, notes folder, or Notion workspace. Setup
will create what it needs.

## Install it

In a Claude Code session, run:

```
/plugin marketplace add shmattox/aios
/plugin install aios
```

Then run:

```
/aios:setup
```

That's it — just run `/aios:setup` and tell me where it stumbles. Don't try
to work around anything that looks broken or confusing; that's exactly what
I want to hear about.

## What to watch for and note down

Setup walks through a few phases. For each one, jot down what it showed you
and anything that felt unclear, slow, wrong, or scary:

1. **Detection** — it should figure out your OS and which tools (Notion,
   Gmail, Drive, etc.) it can see.
2. **Discovery / profile interview** — it'll ask you questions about how you
   want to use it. Note any question that was confusing or that you weren't
   sure how to answer.
3. **Scaffolding** — it writes some files and (if applicable) creates
   folders. Take a look at the generated `CLAUDE.md` it produces — does it
   read sensibly? Does it match what you told it during the interview?
4. **Smoke test** — near the end it runs itself once on a sample item and
   shows you a "verdict" — what it would or wouldn't have done
   automatically. Copy/paste that verdict display for me.
5. **If you're on Windows** — setup will show you some "dry-run" lines
   before it registers anything scheduled, and afterward you can confirm
   what it registered with:
   ```powershell
   Get-ScheduledTask -TaskName "AIOS *"
   ```
   You should see 7 tasks: capture-router, session-capture, ingest,
   gate-auto, garden, resolve-sweep, and brief-cache. (The exact count is
   whatever `python engine/tools/task_manifest.py` prints — it's derived
   from the manifest, so it grows as tasks are added.) Send me that output too.

Basically: screenshot or paste each phase's output as you go, rather than
waiting until the end. If something errors out, the error text itself is
the most useful thing you can send me.

## Known limits in this version — not bugs, just not built yet

- **Mac/Linux:** nothing runs automatically in the background yet. You run
  the pipeline yourself, in a session, whenever you want it to check for
  new items. (Windows gets automatic overnight runs via Task Scheduler.)
- There's a cloud-hosted variant (no always-on computer needed) but it's
  not wired up for self-service yet — someone has to set it up by hand.
- The daily summary ("brief") is noticeably better if you connect Notion —
  without it, it still works, just with less context.
- The auto-approval feature (letting low-risk items ship without asking
  you) only runs on a schedule on Windows. Elsewhere you trigger reviews
  yourself.

None of these should block you from completing setup — just don't be
surprised by them.

## Uninstalling when you're done

1. If you're on Windows and it registered scheduled tasks, unregister them:
   ```powershell
   powershell -File "path\to\deploy\windows\unregister-tasks.ps1"
   ```
2. Remove the local config it created:
   ```
   rm -rf ~/.aios
   ```
3. Remove the plugin itself:
   ```
   /plugin uninstall aios
   ```

This won't touch any notes or Notion pages it created for you along the
way — those are yours to keep or delete separately.

## Send it back to me

Whatever you collected — screenshots, pasted output, "I got stuck at step
X," "this question didn't make sense," anything — just send it back,
including the good parts. I'd rather hear "this was confusing" now than
after ten more people hit the same wall.

Thanks for doing this.
