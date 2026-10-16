<!-- Copyright 2026 Anthropic PBC -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Notes for Claude Code

This directory is a workshop. The person you are working with is here to learn Claude Managed Agents. You write the API code in `agent.py`; they make the decisions that need judgment. Work one part at a time.

## The job

`agent.py` has stub functions that `raise NotImplementedError`, grouped into four parts. When asked to do a part:

1. Read the stub comments for that part. They give the exact call shapes and the values you cannot look up (model constants, mount paths, the beta header). Use them as written.
2. Implement only that part's functions, plus the small additions the comments name in earlier functions (the roster in `setup_agent()`, the memory resources in `start_session()`, the second skill).
3. Run `python check.py --part N`. It drives the live API and prints PASS or FAIL per part. Fix what fails and run it again.
4. Explain what you did in four short bullets: what changed, the Managed Agents concept it uses, where to see it in the dashboard, and which file they should now edit themselves.

## Rules

- Edit `agent.py` only, unless asked. `provided.py`, `state.py`, `check.py` and `cleanup.py` are fixed.
- Do not open `solutions/`. It is the answer key. Work from the stub comments and the installed `anthropic` package.
- Create cloud resources only through `state.sync(...)`, with `state.name(...)` in every `name=` and `state.owner()` in every `metadata=`. A whole room may share one API key, and this is what keeps people's resources apart.
- Never print, log or paste the API key. Never put it in a prompt, an event or a resource.
- Do not delete or archive anything through the API yourself. `python cleanup.py` does that safely.
- Use the platform's exact names when you explain things: agents, environments, sessions, events, skills, custom tools, multiagent, threads, memory stores, session outputs.

## What the person edits

These files are theirs. Point them at the right one after each part, and let them write it.

| Part | Their decision | File |
|---|---|---|
| 1 | What the commander is told | `prompts/commander.md` |
| 2 | How an investigator is briefed and what it hands back | `prompts/investigator.md` |
| 3 | What the team already knows | `memory-seed/*.md` |
| 4 | What goes in the postmortem | `skills/postmortem-report/` |

Changes take effect on the next dashboard rerun or `check.py` run: a changed prompt becomes a new agent version, a changed skill folder becomes a new skill version, and a changed `memory-seed/` becomes a freshly seeded store. Sessions already running keep what they started with, so start a new session to see a change.

## Useful facts

- Run the dashboard with `streamlit run app.py`. The chat panel shows which parts are on.
- The log is mounted in the container at `/mnt/session/uploads/app.log`.
- Custom tools run on this machine in `handle_tool()`. A sub-agent's custom tool call arrives on the session stream with `session_thread_id` set; answer it like any other.
- Memory stores attach when a session is created, not afterwards.
- Files the agent writes under `/mnt/session/outputs/` appear in `files.list(scope_id=session_id)` a few seconds after the turn ends.
- Tested with `anthropic==0.116.0`. If a call shape in a stub comment disagrees with a newer SDK, say so instead of guessing.
