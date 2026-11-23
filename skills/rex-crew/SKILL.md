---
name: rex-crew
description: "Run Claude Code workers in Rex panes on this machine or another Rex host and stay the planner: one call sends a task, waits on the worker's own status, answers routine permission prompts from a policy, hands every other question back to you, and returns a written report plus what is really on disk. Use for 'run this on the build machine', 'start an agent on HOST', 'orchestrate workers over Rex', 'delegate and report back', or when a worker in a Rex pane waits for an answer. Not for plain Rex tabs, splits or commands: use the rex skill."
---

# rex-crew

You plan. Workers build, in Rex panes, on this machine or on another Rex host. Workers never run with
permissions bypassed: every prompt is either covered by a policy you wrote down, or it comes to you as text.

`here` is the `scripts/` directory beside this file. Everything is `"$here/rex-crew" ...`.

## The one call

```sh
export REX_CREW_HOST=buildbox                                  # omit to work on this machine
export REX_CREW_SESSION=$("$here/rex-crew" session my-task)    # a session on that host, created if missing

"$here/rex-crew" run fix-retry /abs/path/on/host/app task.md \
  --policy policy.json --verify 'npm test' --trust \
  -- --permission-mode acceptEdits
```

`run` puts `task.md` (or `-` for stdin) on the host, appends the report protocol, starts Claude Code in a new
tab with the task as its first prompt, and waits. Its output is all you need to read:

```text
state: done   184s
auto-approved (3): Bash: git status | Bash: npm test | Bash: git commit -m ...
git: 4be2c1a retry: back off on 429 · dirty 0
verify: exit 0
report:
STATUS: done
CHANGED: src/retry.ts: waits and retries on 429 instead of failing
VERIFIED: npm test -> 212 passed; did NOT run the e2e suite
COMMIT: 4be2c1a retry: back off on 429
OPEN: backoff cap of 30s is my choice
NEXT: surface the retry count in the logs
```

| Exit | State | What to do |
|---|---|---|
| 0 | `done` | Read `verify` and `git` before the report: the worker's words are not proof. |
| 2 | `blocked` | A question for you, printed as `needs you (...)`. Answer it, then resume. |
| 3 | `timeout` | Still working. `run ... --block BLOCK` waits again. |
| 4 | `error` | The worker reported an error; `screen BLOCK` shows it. |
| 5 | `gone` | The pane is closed. |

## When a worker needs you

```sh
"$here/rex-crew" approve BLOCK --match 'docker compose up'   # one-time Yes, only if the prompt still matches
"$here/rex-crew" deny BLOCK                                  # Escape; then `send` what to do instead
"$here/rex-crew" send BLOCK "Use the staging database."      # only when the worker is at its prompt
"$here/rex-crew" run NAME DIR - --block BLOCK --policy policy.json --verify '...'   # resume: wait, verify, report
```

Approve only what the user's request covers. Deleting, pushing, credentials, anything outside the task
directory: ask the user first. `kind` tells you what is open: `permission`, `question`, `auth`, or `startup`
(a dialog before the agent runs, shown as its screen).

## Policy

A JSON file with two lists of regular expressions, matched against the prompt text such as `Bash: git status`:

```json
{ "approve": ["^Bash: (git (status|log|diff)|npm test)\\b"],
  "never":   ["\\b(rm|sudo|curl)\\b", "git push", "[;&|`$]"] }
```

A prompt is answered for you only if an `approve` pattern matches and no `never` pattern does. It is re-read
immediately before the key is pressed, and the answer is always the one-time Yes, never "always".
Without `--policy`, nothing is answered for you. Start from `policy.example.json` and keep the list short.

## Other commands

| Command | |
|---|---|
| `status [--all]` | Every reporting pane: state, label, block, open question. |
| `wait BLOCK [--policy F] [--timeout S]` | Wait for one turn without the report step. |
| `screen BLOCK [LINES]` | The visible end of a pane. Agents draw on the alternate screen, so there is no scrollback: long answers belong in the report file. |
| `close BLOCK` | Close a worker's pane. `run` closes it itself unless `--keep`. |
| `session LABEL` | The id of the session with that label on the host, created if missing. |

## Rules that keep this cheap and safe

1. **One `run` per task, and write the task as a file.** Say what done means and how to check it. The worker
   cannot see your conversation.
2. **Read facts before prose.** `verify` and `git` come from the host, the report from the worker.
3. **Never pass `--dangerously-skip-permissions` or `bypassPermissions`.** Use `acceptEdits` plus a policy.
4. **One worker per directory at a time.** Give parallel workers their own checkout or worktree, and copy
   generated files they need (project files, `.env.example`) into it first.
5. **Paths are the host's, and absolute.** `$HOME` and `~` are not expanded in `DIR`.
6. **Never type into your own pane.** `send`, `approve`, `deny` and `close` refuse `$REX_BLOCK`.

## What tends to go wrong

- A host that rewrites shell commands through a hook shows them rewritten in the prompt (`rtk git status`).
  Write the policy for what the prompt says.
- Plugins and hooks of the host's Claude Code apply to workers. Turn one off per run with
  `-- --settings '{"enabledPlugins":{"NAME@MARKET":false}}'`.
- A worker started in a folder for the first time asks whether to trust it. `--trust` confirms that dialog only
  if it names `DIR`.
- `state: done` with `(the worker wrote no report file)`: it ended its turn early. `screen`, then `send`.
