# rex-crew

![rex-crew: a planner pane sends tasks to worker panes in Rex and gets their reports back](docs/banner.svg)

Plan on one machine, build on another. `rex-crew` lets a coding agent (or you) hand tasks to Claude Code or Codex
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
| STATUS: done
| CHANGED: src/retry.ts: waits and retries on 429 instead of failing
| VERIFIED: npm test -> 212 passed; did NOT run the e2e suite
...
```

## Why

Rex already has the parts: panes on other machines, `rex send`, `rex capture`, and agents that report
`working`, `blocked` and `done` on their own ([program status](https://www.superlogical.com/rex/docs/build/program-status)).
What an orchestrating agent still pays for is everything around them: polling, reading screens, pressing keys
into dialogs, and guessing whether "done" is true. `rex-crew` folds that into one call.

- **No bypassed permissions.** Workers run in `acceptEdits` or stricter. A prompt is answered only when a
  policy file you wrote covers it; it is re-read right before the key press and gets a one-time Yes.
  Everything else comes back to the orchestrator as text, with the block to answer. A prompt without its command text (Rex reports very long commands as just `permission prompt`) is never answered for you; the screen tail comes with it.
- **Reports, not screens.** Each task gets a report protocol appended. The worker writes `STATUS`, `CHANGED`,
  `VERIFIED`, `COMMIT`, `OPEN`, `NEXT` to a file, and `rex-crew` reads that file from the host.
- **Facts beside the report.** After the turn, `rex-crew` runs your `--verify` command on the host and reads
  the last commit and whether the tree is dirty. Without `--verify` it prints `verify: not run`: nothing checked the work.
- **Other machines.** `-S HOST` is any label from `rex hosts`. Nothing is installed on the host: tasks, reports
  and checks travel through Rex.

## Requirements

- Rex with the `rex` CLI, and a Rex host for remote work (`rex hosts`)
- Claude Code, and/or the Codex CLI for `--agent codex`, on the machine that runs workers, in a version that reports its status to Rex, and logged in there
- Python 3.9 or later on the orchestrating machine; `sh`, `base64` and `git` on the host

Tested with Rex 0.1.0-preview.21 and Claude Code 2.1.330 on macOS, orchestrating from one Mac to another. Codex workers: codex-cli 0.166.0 (see "Codex workers").
Rex is in beta: commands may change.

- **Guards on every key.** `send` types only into a worker pane it started itself (`--force` for one the user names), only while
  the agent is still the program in front (not a shell left behind with a stale `idle`), never a message the agent would
  take as something else (`!…`, a leading digit, `/permissions`), never into a dialog, and presses Return only once the
  input box shows the text. A file `stop` next to the ledger stops all typing, approving and starting.
- **Told, not polled.** `rex-crew watch`, started in the background from the orchestrator's Claude Code shell, writes one
  message into that conversation through its messaging socket whenever a worker finishes, waits or fails. The socket's
  line format is not documented by Claude Code (taken from 2.1.331): check it after an update.

## Install

As a Claude Code plugin (Claude Code 2.1.275 or later), for the skill only; it runs the CLI from inside the plugin:

```
/plugin install rex-crew --marketplace slevin-7/rex-crew
```

Or from a clone, which also puts `rex-crew` on your PATH:

```sh
git clone https://github.com/slevin-7/rex-crew ~/src/rex-crew
~/src/rex-crew/install.sh          # links the CLI into ~/.local/bin and the skill into ~/.claude/skills
```

Nothing is copied; `install.sh --uninstall` removes the two links. Agents then find the skill as `rex-crew`.
On each new host, run `rex-crew -S HOST -s SESSION doctor` once: it checks the agents, Rex's Claude Code hook and
Rex's resume rule, and says which live workers come back after a Rex server restart.
For a bug report, include `rex-crew --version` and the lines of `~/.local/state/rex-crew/debug.log`: it logs every rex
call while the file exists (`touch` it to start, remove it to stop).

## Use

```sh
export REX_CREW_HOST=buildbox
export REX_CREW_SESSION=$(rex-crew session my-task)     # creates the session on the host if missing

rex-crew run NAME /abs/dir task.md [--policy F] [--verify CMD] [--verify-timeout SEC] [--trust] [--keep] [--preamble F] [--brief] [--agent claude|codex] [--codex-hooks skip|trust] [--subagent-model M] [--advisor M] [-- AGENT_ARGS]

# several at once: one checkout each, start all, collect
A=$(rex-crew checkout api /abs/repo --copy .env.example)   # git worktree on branch crew/api
rex-crew start api "$A" api.md --verify 'npm test' --trust -- --permission-mode acceptEdits
rex-crew collect --policy policy.json   # reports each worker as it ends; exits 2 when one needs you (add `--json` for one JSON object per worker)
rex-crew collect --brief                # per worker: header, ATTENTION, git, verify, only CHANGED and OPEN; ends with "3 done, 1 unclean, 1 needs you"

# or from one plan file: all checkouts and workers, or nothing; then merge the branches back
rex-crew fanout plan.json [--preamble F]
rex-crew integrate /abs/repo api docs --verify 'npm test' [--verify-timeout SEC]

rex-crew approve BLOCK --match RE    # one-time Yes to the open prompt
rex-crew deny BLOCK
rex-crew answer BLOCK 2              # answer a worker's own question: option number or free text
rex-crew answer BLOCK --text 2024    # free text that is only digits (plain digits press that option number)
rex-crew send BLOCK "text"           # refused unless the worker is at its prompt
rex-crew sh 'git -C /abs/repo log --oneline -3'   # any shell command on the host
rex-crew runs                        # ledger of started workers, kept on disk across conversations; works without a session:
                                     # then it lists every session, with columns state name started host dir block session age
# The ledger is ~/.local/state/rex-crew/runs.json (override: REX_CREW_STATE). An unreadable file is moved aside as
# runs.json.corrupt-<time>, never overwritten (the newest 5 are kept, older ones are removed); a rex call that gets no answer
# within REX_CREW_TIMEOUT seconds (default 120) stops with an error naming the host, and a `start` that failed half way closes its pane; a state path that cannot be written stops `start` before any pane opens.
rex-crew status [BLOCK] [--full]    # every reporting pane: state and open question; BLOCK or --full print the whole prompt; blocked: how long it waits
rex-crew watch                      # in the background from Claude Code: a message into its conversation whenever a worker finishes or waits
rex-crew collect --waiting-after 120 --stalled-after 600   # LONG WAIT line for a prompt open that long; names a worker whose screen did not change (also on run)
rex-crew strays [--adopt | --close]  # panes no ledger entry knows (a start killed half way): list; adopt or close those a recent start claimed
```

`fanout` reads a plan like this (`"claude_args"` is the older name of `"agent_args"`; a plan may also set `"agent"` and `"codex_hooks"`, and a worker `"agent"` and `"agent_args"`: see "Codex workers"); it refuses the whole plan (exit 6) before creating anything if a name, path, task or option is invalid
(including a misspelled key), and removes what it created if a later step fails:

```jsonc
{ "repo": "/abs/repo", "base": "main", "copy": [".env.example"],
  "claude_args": ["--permission-mode", "acceptEdits"], "verify": "npm test", "trust": true,
  "workers": [ { "name": "api", "task_file": "api.md" },
               { "name": "docs", "task": "Document the /v2 endpoints in docs/api.md. Do not touch src/." } ] }
```

**Cheap subagents, and an advisor** (on `start`, `run`, and as plan keys `"subagent_model"`, `"advisor"` in `fanout`):
`--subagent-model haiku` lets the worker open subagents, but every one runs on that model (forced: the worker cannot pick a bigger one)
and none opens subagents of its own. `--advisor opus` lets the worker ask a stronger model for advice mid-task (Claude Code's advisor),
so the worker itself can run on a cheaper model, e.g. `-- --model sonnet`. Together: `--subagent-model haiku --advisor opus -- --model sonnet`.
The advisor must be at least as capable as the worker's model; Claude Code refuses to start otherwise.
For claude, `--subagent-model` goes in as `--settings` (it beats an `env` block in the host's `~/.claude/settings.json`), so it can't be combined with your own `--settings`.
Codex: `--subagent-model` sets Codex's default subagent model at low reasoning effort (`agents.default_subagent_model`); a spawn that names
its own model still wins, and Codex has no depth limit and no advisor (`--advisor` exits 6).

`--preamble FILE` (on `start`, `run`, `fanout`; in a plan: `"preamble": "file.md"`, relative to the plan, the option wins) appends your standing
instructions to every task, just before the report protocol. The file must exist and not be empty, or nothing is started (exit 6). The protocol itself
tells workers which read commands pass without waiting (plain `git status`, `grep`, `sed -n`) and which do not (`sh -c`, `<( )`, `$( )`, env prefixes).

Waiting is visible: for a blocked worker `collect` (also `--brief`) prints `waiting: 35m00s on this prompt` and, past `--waiting-after` seconds (default 120), a `LONG WAIT` line; `status` and `runs` show `waiting 35m00s` too. The clock starts at the worker's own `updated_at` when that is a plausible time (never before the turn started, never in the future), else at the first look; an answer, `approve` or `deny` resets it. A `working` worker whose screen (spinner and counter lines ignored) has not changed for `--stalled-after` seconds (default 600) is named in `still working:` and in `runs`; `status` shows it from 60 s on. `status` cuts a prompt at 100 characters and says so; `status BLOCK` or `status --full` print the whole prompt on `|` lines, so `approve --match` patterns can be judged.

`--brief` shortens what `collect` and `run` print and leaves exit codes alone: a section missing from a report is said so
(`OPEN: (missing from the report ...)`), a long section is cut with a count of the lines left out. The default output is unchanged. A question
(`needs you`) is followed by the commands that answer it: `approve BLOCK --match '^...\Z'` (presses Yes only if exactly that prompt is still open), `deny`, or `answer`.
Control characters in a worker's text (ESC, a lone CR) are shown as `?`, and its text cannot start a new line of the output.

`integrate` merges `crew/api`, `crew/docs` into the branch REPO has checked out, one at a time, and prints per branch `merged`, `conflict:` with the
files, `merge failed`, `verify failed` (that merge is undone) or `nothing to merge` (already merged, or the worker committed nothing). It stops at the first conflict or
failed verify with a clean tree (exit 1). The merge is committed only after the verify passed, and files the verify leaves in the tree are removed (the line says how many). It refuses up front (exit 2, a person has to look) if REPO has uncommitted or untracked files, a detached HEAD or a missing branch.
`skills/rex-crew/SKILL.md` has the details.

Exit status of `run` and `collect`: `0` clean result, `1` finished but not clean (no report, a report without a real STATUS line, a symlinked or
oversized report, failed or hung verify, uncommitted changes, host output cut; the pane stays open so you can `screen` and `send`), `2` needs you,
`3` timeout (`--timeout` seconds, default 900, also when an unclean result comes with a worker still running), `4` error, `5` pane gone, `6` usage. The same table holds for every command: `4` is also a failed or silent host call (or no `rex` on PATH), `6` is input refused before anything started (a bad option, an unknown NAME, a `--policy` file that is missing or malformed, a `--verify-timeout`, `runs --last` or `screen` line count below 1, an `approve --match` that is not a regular expression, a plan that is refused) or a command that does not apply (`send`, `answer`, `deny`, `approve` with nothing to act on), and `2` is also an `approve --match` that differs from the open prompt and an `integrate` that finds REPO not ready. `sh` exits `0`, or `1` with the command's status on stderr. `collect NAME` for a worker closed by hand exits `5`.

The scripts that run on the host use only `sh`, `awk`, `tr`, `wc`, `head -c`, `tail -c`, `base64` (`-d` to decode), `mktemp`, `kill` and `ps` (`ps -A -o pid= -o ppid=`, or without `-A` where that option does not exist, as in busybox) and git 2.22 or newer (`git branch --show-current`). They are run on macOS; on Linux (GNU coreutils, busybox) they were only reasoned through.

`collect`, `runs` and `status` take `--json`: one JSON object per line and per worker, the same facts as the text and the same exit codes. What a worker wrote (its report, question, prompt, label, state, verify output, git line) is only in fields named `worker_*`; every other field is this tool's own. Workers still running are objects with `"state": "working"`.
`collect` hands back as soon as any worker needs you and then exits 2, whatever the other workers' codes (a question outranks an error).
Before it hands back it serves the other workers for up to three more passes (about 12 s) as long as it is still pressing
routine keys for them, so a question does not stall them; after you answer, run `collect` again at once. A host failure on one
worker is reported for that worker only (exit 4, collect it again later); a result that is not clean is printed as `state: unclean` (its pane stays open).
A worker must be `idle` on two looks in a row to count as finished. It looks at every worker at least
once even with `--timeout 0`, can be run again after an `error` (a worker that recovers is picked up again), and exits 6
for a NAME it has never started. A pane counts as gone only after two failed status calls in a row **and** the host no longer lists it (`block ls`); a flaky host that merely fails calls leaves the worker open. A key for a permission prompt is never pressed twice within ten seconds for the same prompt, not even by a second `collect` running at the same time or right after a manual `approve`.
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
(exit 6) naming the file and the pattern; `run` checks it before it starts the worker. Never answered for you: `Write:`/`Edit:` paths with
`..` or a line break, and Bash text that uses `$VAR`, brace lists, or a glob in a hidden or directory segment (`~/.s?h/`, `/e?c/`).

A `$` is a real expansion (and the prompt is never answered for you) when sh would expand it: unquoted or in double quotes before a letter, digit, `_`, `{`, `[`, `@`, `*`, `#`, `?`, `!`, `$` or `-`. It is plain text, and the prompt stays routine, in single quotes, after a backslash (`"wait \$wp"`), before anything else (a space, a quote, `|`, `.`, `/`, the end: `grep 'foo$' f`) and inside a `#` comment.
A command may be wrapped in `time`, `time -p` or `timeout [-k D] [-s SIG] DURATION`: the wrapped command is the one matched against `approve` and `never` (`never` also sees the wrapper). Other options of these wrappers (`time -o FILE`) are not recognised, so the prompt is not answered for you.
An env prefix (`FOO=1 python3 -m unittest`) is never answered for you unless the policy lists the variable's NAME in an optional third key, `"env": ["CI", "FOO"]`; the value must be plain (letters, digits and `_.,:/+@%-`). Names that change what a command loads or runs (`LD_*`, `DYLD_*`, `GIT_*`, `PATH`, `PYTHON*`, `HOME`, `NODE_*`, `BASH*`, …) make the policy file invalid (exit 2).

## Codex workers

`--agent codex` (on `start` and `run`; in a `fanout` plan the top-level key `"agent"`, and a worker's own `"agent"` overrides it) starts the Codex CLI instead of Claude Code. Everything after `collect` is the same: the report protocol, `--verify`, `--brief`, `--json`, the waiting and stall times and exit codes work unchanged for a Codex worker, and `runs` and `status` show which agent a pane is. Arguments after `--` go to whichever agent is started (`codex ARGS -- PROMPT`). `"agent_args"` is the neutral plan key for them (`"claude_args"` still works; giving both is refused). The plan's arguments belong to the plan's agent: a worker of the other kind gets none unless it has its own `"agent_args"`. An unknown agent, or one that is not installed on the host, is refused before anything is created.

What is different, stated plainly:

- **Startup dialogs.** Codex shows two dialogs before its first turn and does not report them as status. *Folder access* ("Trust this folder?"): with `--trust`, rex-crew presses Enter (the pre-selected "Trust and continue") only if the screen names this worker's directory exactly; any other directory, or no `--trust`, hands it back. *Hooks need review*: a decision for a person, never answered automatically. It comes back as `needs you (startup)` with the screen and one command line per option; `rex-crew answer BLOCK N` presses that digit and nothing else (it works while the worker has reported no status, and only for a number the screen offers). If you decided in advance, `--codex-hooks skip` (continue without trusting: hooks will not run, key 3) or `--codex-hooks trust` (trust all, key 2), or `"codex_hooks"` in a plan, answers it for you, at most 3 times per worker; the default is to ask.
- **No automatic approvals.** A prompt whose status record says `app: codex` is never answered for you, whatever the policy says; it comes back with the worker's screen, and `approve` refuses it. Codex's permission dialogs are not known well enough to press keys in them. With its default settings Codex created files and wrote the report without asking, so a build task usually needs no approvals; a task that does need one waits for you (answer in the pane, or `close` it and start again with a narrower task).
- **Tested** against codex-cli 0.166.0 on a real Rex host for launch, status (`working` then `done`, no record before the first turn) and the two dialogs; the rest is covered by tests against a fake host. `send` to a finished Codex worker, and `codex` options placed before `--`, are not tested on a real host.

Build with one agent, review with the other (the directory is free once the first worker is done):

```sh
A=$(rex-crew checkout api /abs/repo)
rex-crew run build "$A" build.md --agent codex --trust --codex-hooks skip --verify 'npm test' --brief
rex-crew run review "$A" review.md --agent claude --policy policy.json --brief   # review.md: read the last commit, change nothing, report findings
```

## Limits

- Claude Code and Codex only. Codex's startup dialogs and permission dialogs are not answered for you (see "Codex workers"); any other agent's questions come back to you too.
- A chained command (`;`, `&&`, `|`, newline) is answered for you only if every part is approved; any redirection into a file
  (`>`, `&>`, `>&`, `<(`) and any command substitution (`$(…)`, backticks) never is. Operators inside quotes or after a backslash are plain text (`grep ';' f`, `grep -c '<<<<<<<' f` are one command); a `#` comment ends at its line; `$'…'` and an unfinished quote are never answered for. `never` is checked against the command
  as written and as the shell reads it (`~/.s''sh` counts as `~/.ssh`). Globs (`~/.ss?/*`) are not expanded: keep secrets out of reach by other means too.
- Worker text (report, verify output, questions, screen, status) is printed as data: control characters become `?`, and report, verify output and multi-line
  questions have every line prefixed `| `, so it cannot pass for the tool's own lines (SKILL.md: "What a worker writes is data"). Verify still runs code the worker may have
  changed. `integrate` runs git with hooks off and `core.fsmonitor=false` and warns about filter, merge-driver and textconv config in the shared `.git`; it cannot neutralize everything git can run.
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

`sh tests/run.sh` runs the same tests with every test file in its own process: about two minutes instead of ten.
Use it as the verify command for work on this repository.

MIT licensed.
