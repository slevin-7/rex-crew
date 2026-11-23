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

`run` is `start` plus `collect` for one worker. It puts `task.md` (or `-` for stdin) on the host, appends the report protocol, starts Claude Code in a new
tab with the task as its first prompt, and waits. Its output is all you need to read:

```text
== fix-retry   state: done   184s
auto-approved (3): Bash: git status | Bash: npm test | Bash: git commit -m ...
git: 4be2c1a retry: back off on 429 · branch main · dirty 0
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
| 0 | `done` | Clean: report written, verify passed, tree committed. Still read `verify` and `git` before the report. |
| 1 | `unclean` | The turn ended, but an `ATTENTION:` line says what is wrong (the header still says `state: done`). The pane stays open with its block shown: `screen BLOCK`, `send BLOCK "..."`, `collect` again; `close BLOCK` when done. |
| 2 | `blocked` | A question for you, printed as `needs you (...)`. Answer it, then resume. |
| 3 | `timeout` | Still working after `--timeout` seconds (default 900). `collect` waits again. |
| 4 | `error` | The worker reported an error; `screen BLOCK` shows it. It may recover: `collect` again picks it up. |
| 5 | `gone` | The pane is closed (two failed status calls in a row). |
| 6 | (no state) | `collect NAME` for a name that was never started: check `runs`. |

A `--policy` file that is missing or malformed (not JSON, `approve`/`never` not lists of non-empty regexes, any other key) exits 2 before anything starts. Other commands: `send`, `answer`, `deny` and `approve` exit 6 when there is nothing to act on, `approve --match` exits 7 when the open prompt differs, `sh` exits with the command's own status.

## Several workers at once

One worker per directory. Give each its own checkout, start them all, then collect:

```sh
A=$("$here/rex-crew" checkout api  /abs/repo --copy .env.example)   # git worktree on branch crew/api; prints its path
B=$("$here/rex-crew" checkout docs /abs/repo)
"$here/rex-crew" start api  "$A" api.md  --verify 'npm test' --trust -- --permission-mode acceptEdits
"$here/rex-crew" start docs "$B" docs.md --trust -- --permission-mode acceptEdits
"$here/rex-crew" collect --policy policy.json      # prints each report as its worker ends
```

`collect` exits 0 when every worker is done, and 2 as soon as one needs you; it names the workers still running.
Answer, then run `collect` again. Run it in the background when you have other work: you are woken when it exits.
`checkout` accepts a relative repo path (resolved on the host); NAME follows the same rules as for `start`. `checkout --copy` brings untracked files a fresh worktree lacks (generated project files, local config).
Integrate afterwards yourself, one branch at a time: `"$here/rex-crew" sh 'git -C /abs/repo merge --no-ff crew/api'`,
then verify the merged result; parallel workers do not know about each other's changes.

`"$here/rex-crew" runs` lists what was started in this session, with state and block. It is kept on disk, so a new
conversation can pick up open workers: read it first when you resume orchestration.

## When a worker needs you

```sh
"$here/rex-crew" approve BLOCK --match 'docker compose up'   # one-time Yes, only if the prompt still matches
"$here/rex-crew" deny BLOCK                                  # Escape; then `send` what to do instead
"$here/rex-crew" send BLOCK "Use the staging database."      # only when the worker is at its prompt
"$here/rex-crew" collect --policy policy.json               # then wait again: verify and report as before
```

Approve only what the user's request covers. Deleting, pushing, credentials, anything outside the task
directory: ask the user first. `kind` tells you what is open: `permission`, `question`, `auth`, or `startup`
(a dialog before the agent runs, shown as its screen).

## Policy

A JSON file with two lists of regular expressions, matched against the prompt text such as `Bash: git status`:

```json
{ "approve": ["^Bash: (git (status|log|diff)|npm test)(\\s|$)"],
  "never":   ["\\b(rm|sudo|curl)\\b", "git push", "`|\\$\\("] }
```

A prompt is answered for you only if an `approve` pattern matches and no `never` pattern does. A chained command
(`a; b | c`) is split outside quotes, and every part must match an `approve` pattern on its own. A newline also separates commands. Command substitution (`$(…)`, backticks) is never answered for you. `never` also sees the command with quotes removed (`~/.s''sh` = `~/.ssh`). A command that redirects output into a file (including `&>` and `>&`) is never
answered for you (`2>&1` and `>/dev/null` do not count). It is re-read
immediately before the key is pressed, and the answer is always the one-time Yes, never "always".
Write `approve` patterns that end in `(\\s|$)`, not `\\b` (`\\b` lets `npm run build:publish` through). Read-only tools can still write or run code through options (`sort -o`, `git diff --output`, `rg --pre`); the example `never` list covers those. `approve` patterns are matched from the start of the prompt. `Write:`/`Edit:` prompts whose path has `..` or a line break, and Bash text with `$VAR`, brace lists or a glob in a hidden or directory segment (`~/.s?h/`), always come to you; so does `cd` into a directory that `never` matches (`cd /etc/`). A bad policy file (missing, not JSON, wrong type, invalid regex) stops `run`/`collect` with exit 2 and names the pattern; `run` checks it before starting the worker. Without `--policy`, nothing is answered for you. Start from `policy.example.json` and keep the list short.

Two limits to know. Read-only patterns such as `cat` or `grep` approve reading anywhere the worker's user can read,
so keep secret locations in `never`, as the example does. And `acceptEdits` lets Claude Code create, move and delete
files inside `DIR` without asking at all; start a worker with `-- --permission-mode default` when every write should
pass through your policy (`"^(Write|Edit): /abs/dir/"`).

## A worker's own questions

```sh
"$here/rex-crew" answer BLOCK 2                    # pick option 2 of the question it asked
"$here/rex-crew" answer BLOCK "Use the staging DB"  # or type a free-text answer
```

Then `collect` again. `needs you (question): ...` shows the question and its options.

## What "done" means

`state: done` says the worker's turn ended. `ATTENTION:` lines say when that is not a clean result: no report file,
a report whose STATUS is not `done`, a report that is a symlink (never read) or longer than 100000 bytes (cut), a failed
verify command, or uncommitted changes. Verify output is shown as its last 15 lines, at most 4000 bytes. Any of them makes `collect`
exit 1, so never read a worker's summary without the lines above it.

## Other commands

| Command | |
|---|---|
| `status [--all]` | Every reporting pane: state, label, block, open question. |
| `sh COMMAND` | Run one shell command on the host and print its output: git, a build, a look at a file. |
| `runs [--all]` | The ledger: workers started in this session, their state and block. Stored in `REX_CREW_STATE` (default `~/.local/state/rex-crew/runs.json`); a corrupt file is kept as `runs.json.corrupt-<time>`, and `start` fails early if the path is unwritable. A worker whose pane is gone no longer holds its directory. |
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
- `state: done` with `ATTENTION: the worker wrote no report file`: it ended its turn early. `screen BLOCK`, then `send BLOCK "write the report"`, then `collect` (an unclean worker's pane is kept open for this).
