#!/bin/sh
# Resume walkthrough, written from skills/rex-crew/SKILL.md alone: an earlier conversation fanned out four workers and ended.
# Now one is done and clean, one is unclean (no report), one waits on a permission prompt, one pane is gone.
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
here="$root/skills/rex-crew/scripts"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export REX_CREW_STATE="$tmp/runs.json"
mkdir "$tmp/bin" && ln -s "$root/tests/fake_rex" "$tmp/bin/rex"
# the host's agents: start refuses one that is not installed (a CI runner has none)
for a in claude codex; do printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/$a" && chmod +x "$tmp/bin/$a"; done
export PATH="$tmp/bin:$PATH" FAKE_REX_STATE="$tmp/fake.json"
unset REX_BLOCK REX_CREW_HOST REX_CREW_SESSION REX_SESSION
python3 - "$FAKE_REX_STATE" <<'PY'
import json, sys
w, d = {"state": "working"}, {"state": "done"}
perm = {"state": "blocked", "kind": "permission", "msg": "approve Bash: make deploy"}
scripts = {"good": [d], "messy": [d], "ask": [perm], "vanish": [w, "GONE"]}
json.dump({"next": 1, "sessions": [], "scripts": scripts, "blocks": {}, "sent": []}, open(sys.argv[1], "w"))
PY
fail() { echo "WALKTHROUGH FAILED: $*"; exit 1; }

repo="$tmp/repo"
mkdir "$repo" && cd "$repo" || exit 1
git init -q -b main . && git config user.email t@t && git config user.name t
for f in a b c d; do echo base > $f.txt; done
git add . && git commit -qm base
for n in good messy ask vanish; do echo "Write $n.txt and commit it." > "$tmp/$n.md"; done
cat > "$tmp/policy.json" <<'EOF'
{ "approve": ["^Bash: git (status|log|diff)(\\s|$)"], "never": ["git push"] }
EOF
cat > "$tmp/plan.json" <<EOF
{ "repo": "$repo", "base": "main", "trust": true,
  "claude_args": ["--permission-mode", "acceptEdits"],
  "workers": [ {"name": "good", "task_file": "good.md"}, {"name": "messy", "task_file": "messy.md"},
               {"name": "ask", "task_file": "ask.md"}, {"name": "vanish", "task_file": "vanish.md"} ] }
EOF

echo "### conversation 1: fan out, then it ends"
export REX_CREW_SESSION=$("$here/rex-crew" session resume-walk) || exit 1
started=$("$here/rex-crew" fanout "$tmp/plan.json") || exit 1
echo "$started"
dirof() { echo "$started" | awk -v n="$1" '$1=="started" && $2==n {for(i=1;i<=NF;i++) if($i=="dir:") print $(i+1)}'; }
# the workers (played by this script): good and vanish commit with a report, messy commits but writes no report
for n in good messy vanish; do
  ( cd "$(dirof $n)" && echo "$n" > "$n.txt" && git add -A && git commit -qm "work of $n" ) || fail "setup $n"
done
for n in good vanish; do
  mkdir -p "$(dirof $n)/.rex-crew" && printf 'STATUS: done\nCHANGED: %s\nCOMMIT: x\n' "$n" > "$(dirof $n)/.rex-crew/report-$n.md"
done
unset REX_CREW_SESSION

echo "### conversation 2: nothing but the ledger and SKILL.md"
"$here/rex-crew" runs; echo "runs exit $?"
"$here/rex-crew" status; echo "status exit $?"
export REX_CREW_SESSION=s1   # read from the session column of runs
"$here/rex-crew" status; echo "status exit $?"
"$here/rex-crew" collect --policy "$tmp/policy.json" --timeout 20; echo "collect exit $?"
# answer the waiting one with the line collect printed; the worker goes on and ends its turn
"$here/rex-crew" approve b14 --match '^Bash:\ make\ deploy\Z' || fail "approve"
python3 - "$FAKE_REX_STATE" <<'PY'
import json, sys
st = json.load(open(sys.argv[1])); st["blocks"]["b14"]["script"] = [{"state": "working"}, {"state": "done"}]
st["blocks"]["b11"]["output"] = "I changed messy.txt and committed.\n> \n"
json.dump(st, open(sys.argv[1], "w"))
PY
( cd "$(dirof ask)" && echo ask > ask.txt && git add -A && git commit -qm "work of ask" \
  && mkdir -p .rex-crew && printf 'STATUS: done\nCHANGED: ask\nCOMMIT: x\n' > .rex-crew/report-ask.md )
"$here/rex-crew" collect --policy "$tmp/policy.json" --timeout 20 > "$tmp/c2.out"; rc=$?; cat "$tmp/c2.out"; echo "collect exit $rc"
grep -q 'state: unclean' "$tmp/c2.out" || fail "messy should be unclean"
# the unclean one (messy): look at its screen, tell it what to do, collect it again
"$here/rex-crew" screen b11 || fail "screen"
"$here/rex-crew" send b11 "write the report" || fail "send"
mkdir -p "$(dirof messy)/.rex-crew" && printf 'STATUS: done\nCHANGED: messy\nCOMMIT: x\n' > "$(dirof messy)/.rex-crew/report-messy.md"
"$here/rex-crew" collect messy --timeout 20 || fail "collect messy"
# the gone one (vanish): its commit and report are on disk, so it is good; nothing left to close
"$here/rex-crew" runs
echo "### nothing may be left running"
"$here/rex-crew" status | grep . && fail "status still lists a pane"
"$here/rex-crew" runs | awk -F'\t' '$1!="done" && $1!="gone" && $1!="closed"' | grep . && fail "ledger still shows a live worker"
python3 - "$FAKE_REX_STATE" <<'PY' || fail "panes still open"
import json, sys
open_ = [k for k, b in json.load(open(sys.argv[1]))["blocks"].items() if not b["closed"]]
sys.exit(f"open panes: {open_}" if open_ else 0)
PY
echo "### integrate what is good"
"$here/rex-crew" integrate "$repo" good messy ask vanish --verify 'test -f good.txt' || fail "integrate"
for f in good messy ask vanish; do test -f "$repo/$f.txt" || fail "missing $f.txt"; done
for n in good messy ask vanish; do "$here/rex-crew" sh "git -C $repo worktree remove --force $(dirof $n) && git -C $repo branch -d crew/$n" >/dev/null || fail "cleanup $n"; done
echo "tally: 4 workers resumed, collected and integrated"
