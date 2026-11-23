# rex-crew

Plan on one machine, build on another. `rex-crew` lets a coding agent (or you) hand tasks to Claude Code
workers running in [Rex](https://www.superlogical.com/rex) panes, on this machine or on any Rex host, and get
back a short, checkable result instead of a screen to read.

```sh
rex-crew -S buildbox -s "$SESSION" run fix-retry /Users/me/code/app task.md \
  --policy policy.json --verify 'npm test' -- --permission-mode acceptEdits
```

```text
== fix-retry   state: done   184s
auto-approved (3): Bash: git status | Bash: npm test | Bash: git commit -m ...
git: 4be2c1a retry: back off on 429 · branch main · dirty 0
verify: exit 0
report:
STATUS: done
CHANGED: src/retry.ts: waits and retries on 429 instead of failing
VERIFIED: npm test -> 212 passed; did NOT run the e2e suite
...
```

## Why

Rex already has the parts: panes on other machines, `rex send`, `rex capture`, and agents that report
`working`, `blocked` and `done` on their own ([program status](https://www.superlogical.com/rex/docs/build/program-status)).
What an orchestrating agent still pays for is everything around them: polling, reading screens, pressing keys
into dialogs, and guessing whether "done" is true. `rex-crew` folds that into one call.

- **No bypassed permissions.** Workers run in `acceptEdits` or stricter. A prompt is answered only when a
  policy file you wrote covers it; it is re-read right before the key press and gets a one-time Yes.
  Everything else comes back to the orchestrator as text, with the block to answer.
- **Reports, not screens.** Each task gets a report protocol appended. The worker writes `STATUS`, `CHANGED`,
  `VERIFIED`, `COMMIT`, `OPEN`, `NEXT` to a file, and `rex-crew` reads that file from the host.
- **Facts beside the report.** After the turn, `rex-crew` runs your `--verify` command on the host and reads
  the last commit and whether the tree is dirty.
- **Other machines.** `-S HOST` is any label from `rex hosts`. Nothing is installed on the host: tasks, reports
  and checks travel through Rex.

## Requirements

- Rex with the `rex` CLI, and a Rex host for remote work (`rex hosts`)
- Claude Code on the machine that runs workers, in a version that reports its status to Rex, and logged in there
- Python 3.9 or later on the orchestrating machine; `sh`, `base64` and `git` on the host

Tested with Rex 0.1.0-preview.21 and Claude Code 2.1.330 on macOS, orchestrating from one Mac to another.
Rex is in beta: commands may change.

## Install

```sh
git clone https://github.com/slevin-7/rex-crew ~/src/rex-crew
~/src/rex-crew/install.sh          # links the CLI into ~/.local/bin and the skill into ~/.claude/skills
```

Nothing is copied; `install.sh --uninstall` removes the two links. Agents then find the skill as `rex-crew`.

## Use

```sh
export REX_CREW_HOST=buildbox
export REX_CREW_SESSION=$(rex-crew session my-task)     # creates the session on the host if missing

rex-crew run NAME /abs/dir task.md [--policy F] [--verify CMD] [--verify-timeout SEC] [--trust] [--keep] [-- CLAUDE_ARGS]

# several at once: one checkout each, start all, collect
A=$(rex-crew checkout api /abs/repo --copy .env.example)   # git worktree on branch crew/api
rex-crew start api "$A" api.md --verify 'npm test' --trust -- --permission-mode acceptEdits
rex-crew collect --policy policy.json   # reports each worker as it ends; exits 2 when one needs you

# or from one plan file: all checkouts and workers, or nothing; then merge the branches back
rex-crew fanout plan.json
rex-crew integrate /abs/repo api docs --verify 'npm test' [--verify-timeout SEC]

rex-crew approve BLOCK --match RE    # one-time Yes to the open prompt
rex-crew deny BLOCK
rex-crew answer BLOCK 2              # answer a worker's own question: option number or free text
rex-crew send BLOCK "text"           # refused unless the worker is at its prompt
rex-crew sh 'git -C /abs/repo log --oneline -3'   # any shell command on the host
rex-crew runs                        # ledger of started workers, kept on disk across conversations
# The ledger is ~/.local/state/rex-crew/runs.json (override: REX_CREW_STATE). An unreadable file is moved aside as
# runs.json.corrupt-<time>, never overwritten; a state path that cannot be written stops `start` before any pane opens.
rex-crew status                      # every reporting pane: state and open question
```

`fanout` reads a plan like this; it refuses the whole plan (exit 2) before creating anything if a name, path, task or option is invalid
(including a misspelled key), and removes what it created if a later step fails:

```jsonc
{ "repo": "/abs/repo", "base": "main", "copy": [".env.example"],
  "claude_args": ["--permission-mode", "acceptEdits"], "verify": "npm test", "trust": true,
  "workers": [ { "name": "api", "task_file": "api.md" },
               { "name": "docs", "task": "Document the /v2 endpoints in docs/api.md. Do not touch src/." } ] }
```

`integrate` merges `crew/api`, `crew/docs` into the branch REPO has checked out, one at a time, and prints per branch `merged`, `conflict:` with the
files, `merge failed`, `verify failed` (that merge is undone) or `nothing to merge` (the worker committed nothing). It stops at the first conflict or
failed verify with a clean tree (exit 1) and refuses up front (exit 2) if REPO has uncommitted or untracked files, a detached HEAD or a missing branch.
`skills/rex-crew/SKILL.md` has the details.

Exit status of `run` and `collect`: `0` clean result, `1` finished but not clean (no report, a report without a real STATUS line, a symlinked or
oversized report, failed or hung verify, uncommitted changes, host output cut; the pane stays open so you can `screen` and `send`), `2` needs you,
`3` timeout (`--timeout` seconds, default 900), `4` error, `5` pane gone, `6` unknown NAME. A `--policy` file that is missing or malformed exits `2` before any pane opens.
`collect` hands back as soon as any worker needs you and then exits 2, whatever the other workers' codes (a question outranks an error).
Before it hands back it serves the other workers for up to three more passes (about 12 s) as long as it is still pressing
routine keys for them, so a question does not stall them; after you answer, run `collect` again at once. A host failure on one
worker is reported for that worker only (exit 4, collect it again later); a result that is not clean is printed as `state: unclean` (its pane stays open).
A worker must be `idle` on two looks in a row to count as finished. It looks at every worker at least
once even with `--timeout 0`, can be run again after an `error` (a worker that recovers is picked up again), and exits 6
for a NAME it has never started. A pane counts as gone only after two failed status calls in a row.
`skills/rex-crew/SKILL.md` is the full guide, written for the orchestrating agent.

## Policy

```json
{ "approve": ["^Bash: (git (status|log|diff|add|commit)|npm test)(\\s|$)"],
  "never":   ["\\b(rm|sudo|curl)\\b", "git push", "`|\\$\\("] }
```

Patterns are matched against the prompt as the agent reports it, such as `Bash: git status`. `never` wins.
[`policy.example.json`](policy.example.json) is a starting point. This is a convenience for routine prompts,
not a sandbox: a pattern that is too wide approves too much. `approve` patterns are matched from the start of the prompt (write `^`
anyway). A policy file that is missing, not JSON, of the wrong shape or has an invalid pattern stops `run` and `collect` with a clear error
(exit 2) naming the file and the pattern; `run` checks it before it starts the worker. Never answered for you: `Write:`/`Edit:` paths with
`..` or a line break, and Bash text that uses `$VAR`, brace lists, or a glob in a hidden or directory segment (`~/.s?h/`, `/e?c/`).

## Limits

- Claude Code only. Other agents' dialogs are not answered; their questions still come back to you.
- A chained command (`;`, `&&`, `|`, newline) is answered for you only if every part is approved; any redirection into a file
  (`>`, `&>`, `>&`, `<(`) and any command substitution (`$(…)`, backticks) never is. `never` is checked against the command
  as written and as the shell reads it (`~/.s''sh` counts as `~/.ssh`). Globs (`~/.ss?/*`) are not expanded: keep secrets out of reach by other means too.
- Workers do not message each other. The orchestrator passes results on, usually as the next task's input.
- Every host step opens and closes a short-lived tab named `crew-sh`.

## Related

- [rex-orchestrator](https://github.com/mkltxo/rex-orchestrator): the same job on one machine, with a macOS
  sandbox and write claims per directory. Its guarded sending and one-time answers shaped this tool.
- [rex-inbox](https://github.com/captjt/rex-inbox): an inbox for you, with a Mac app and a phone view. Its
  re-read-before-approve rule is used here.
- [rex-agent](https://github.com/luongnv89/skills): a worker fleet tiled beside the orchestrator.
- [Axel](https://axel.md): named agents and threads on Rex, built for a person in the loop.

Not affiliated with Superlogical.

## Development

```sh
skills/rex-crew/scripts/rex-crew selftest
python3 -m unittest discover -s tests -v   # needs no Rex: uses tests/fake_rex
```

MIT licensed.
