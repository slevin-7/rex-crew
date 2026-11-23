# rex-crew

Plan on one machine, build on another. `rex-crew` lets a coding agent (or you) hand tasks to Claude Code
workers running in [Rex](https://www.superlogical.com/rex) panes, on this machine or on any Rex host, and get
back a short, checkable result instead of a screen to read.

```sh
rex-crew -S buildbox -s "$SESSION" run fix-retry /Users/me/code/app task.md \
  --policy policy.json --verify 'npm test' -- --permission-mode acceptEdits
```

```text
state: done   184s
auto-approved (3): Bash: git status | Bash: npm test | Bash: git commit -m ...
git: 4be2c1a retry: back off on 429 · dirty 0
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

rex-crew run NAME /abs/dir task.md [--policy F] [--verify CMD] [--trust] [--keep] [-- CLAUDE_ARGS]
rex-crew status                      # every worker: state and open question
rex-crew approve BLOCK --match RE    # one-time Yes to the open prompt
rex-crew deny BLOCK
rex-crew send BLOCK "text"           # refused unless the worker is at its prompt
rex-crew run NAME /abs/dir - --block BLOCK ...   # resume after answering
```

Exit status of `run` and `wait`: `0` done, `2` needs you, `3` timeout, `4` error, `5` pane gone.
`skills/rex-crew/SKILL.md` is the full guide, written for the orchestrating agent.

## Policy

```json
{ "approve": ["^Bash: (git (status|log|diff|add|commit)|npm test)\\b"],
  "never":   ["\\b(rm|sudo|curl)\\b", "git push", "`|\\$\\("] }
```

Patterns are matched against the prompt as the agent reports it, such as `Bash: git status`. `never` wins.
[`policy.example.json`](policy.example.json) is a starting point. This is a convenience for routine prompts,
not a sandbox: a pattern that is too wide approves too much.

## Limits

- Claude Code only. Other agents' dialogs are not answered; their questions still come back to you.
- One turn per `run`. A worker that needs several rounds is resumed with `--block`.
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
```

MIT licensed.
