---
name: Bug report
about: Something isn't working as expected
title: ""
labels: bug
assignees: ""
---

**Describe the bug**
A clear, concise description of what's wrong.

**To reproduce**
Steps to reproduce the behavior.

**Expected behavior**
What you expected to happen instead.

**Environment**
- Rivulets version: Settings → Updates (there is no `rivulets --version` CLI)
- OS / arch:
- Browser (if UI-related):
- Install method: binary / Docker / source

**Logs**
Rivulets does not currently write a log file under `~/.rivulets/logs/` (that directory is created but unused). Paste the relevant stdout/stderr from the process (terminal, `docker logs`, etc.). Redact anything sensitive (API keys are never logged, but message content might be).

**Additional context**
Anything else that seems relevant.
