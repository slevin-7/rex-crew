#!/bin/sh
# Lessons walkthrough, written from skills/rex-crew/SKILL.md alone. Needs (b), (d) and (f):
#  (f) a worker is started without verify: its result must say so; a worker whose verify fails is not accepted
#  (d) a worker reports nothing new: it is waiting, not working; look at its screen
#  (b) two workers rewrote the same line: no merge; a third carries the second one's work over, tests unchanged
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
here="$root/skills/rex-crew/scripts"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export REX_CREW_STATE="$tmp/runs.json"
mkdir "$tmp/bin" && ln -s "$root/tests/fake_rex" "$tmp/bin/rex"
export PATH="$tmp/bin:$PATH" FAKE_REX_STATE="$tmp/fake.json"
unset REX_BLOCK REX_CREW_HOST REX_CREW_SESSION REX_SESSION
python3 - "$FAKE_REX_STATE" <<'PY'
import json, sys
w, d = {"state": "working"}, {"state": "done"}
scripts = {"alpha": [d], "beta": [d], "quiet": [w], "carry": [d], "plain": [d]}
json.dump({"next": 1, "sessions": [], "scripts": scripts, "blocks": {}, "sent": []}, open(sys.argv[1], "w"))
PY
fail() { echo "WALKTHROUGH FAILED: $*"; exit 1; }

repo="$tmp/repo"
mkdir "$repo" && cd "$repo" || exit 1
git init -q -b main . && git config user.email t@t && git config user.name t
printf 'def rate():\n    return 1\n' > f.py
printf 'import f\nassert f.rate() > 0, "rate must be positive"\n' > test_f.py
git add . && git commit -qm base
cat > "$tmp/policy.json" <<'EOF'
{ "approve": ["^Bash: git (status|log|diff)(\\s|$)", "^Bash: python3 test_f.py(\\s|$)"], "never": ["git push"] }
EOF
echo "Rewrite rate() to return 2. Run python3 test_f.py; commit only when green." > "$tmp/alpha.md"
echo "Rewrite rate() to return 3. Run python3 test_f.py; commit only when green." > "$tmp/beta.md"
echo "Wait for the owner of the lock before you go on." > "$tmp/quiet.md"
cat > "$tmp/plan.json" <<EOF
{ "repo": "$repo", "base": "main", "trust": true, "verify": "python3 test_f.py",
  "claude_args": ["--permission-mode", "acceptEdits"],
  "workers": [ {"name": "alpha", "task_file": "alpha.md"}, {"name": "beta", "task_file": "beta.md"},
               {"name": "quiet", "task_file": "quiet.md"} ] }
EOF

echo "### fan out three workers"
export REX_CREW_SESSION=$("$here/rex-crew" session lessons) || exit 1
started=$("$here/rex-crew" fanout "$tmp/plan.json") || fail "fanout"
echo "$started"
dirof() { echo "$started" | awk -v n="$1" '$1=="started" && $2==n {for(i=1;i<=NF;i++) if($i=="dir:") print $(i+1)}'; }
blockof() { echo "$started" | awk -v n="$1" '$1=="started" && $2==n {for(i=1;i<=NF;i++) if($i=="block:") print $(i+1)}'; }
# the workers, played by this script: alpha and beta each rewrite rate() and commit with a report
for n in alpha beta; do
  v=2; [ $n = beta ] && v=3
  ( cd "$(dirof $n)" && printf 'def rate():\n    return %s\n' $v > f.py && git add -A && git commit -qm "rate $n" \
    && mkdir -p .rex-crew && printf 'STATUS: done\nCHANGED: f.py\nCOMMIT: x\n' > .rex-crew/report-$n.md ) || fail "setup $n"
done

echo "### (d) one worker says nothing new: collect, then look at its screen"
"$here/rex-crew" collect --policy "$tmp/policy.json" --timeout 8 > "$tmp/c1.out"; rc=$?; cat "$tmp/c1.out"
[ $rc -eq 3 ] || fail "a silent worker should end collect with exit 3, got $rc"
grep -q 'still working: quiet' "$tmp/c1.out" || fail "quiet is still working"
grep -q 'verify: exit 0' "$tmp/c1.out" || fail "alpha and beta verified"
q=$(blockof quiet)
python3 - "$FAKE_REX_STATE" "$q" <<'PY'
import json, sys
st = json.load(open(sys.argv[1])); st["blocks"][sys.argv[2]]["output"] = "Waiting for you to confirm. Continue? (y/n)\n"
json.dump(st, open(sys.argv[1], "w"))
PY
"$here/rex-crew" screen "$q" | grep -q 'Continue' || fail "screen shows what it waits on"
# send refuses a pane that reports working; a waiting worker is closed and restarted with a narrower task
"$here/rex-crew" send "$q" "y" 2>/dev/null && fail "send must refuse a pane that reports working"
"$here/rex-crew" close "$q" || fail "close"

echo "### (f) a worker started without verify must say so"
d=$(cd "$tmp" && "$here/rex-crew" checkout plain "$repo") || fail "checkout"
echo "Add a comment line to f.py." > "$tmp/plain.md"
"$here/rex-crew" start plain "$d" "$tmp/plain.md" --trust -- --permission-mode acceptEdits >/dev/null || fail "start plain"
mkdir -p "$d/.rex-crew" && printf 'STATUS: done\nCHANGED: f.py\nCOMMIT: x\n' > "$d/.rex-crew/report-plain.md"
"$here/rex-crew" collect plain --timeout 8 > "$tmp/c2.out"; cat "$tmp/c2.out"
grep -q 'verify: not run' "$tmp/c2.out" || fail "a result without a verify must say it was not verified"

echo "### (b) two workers rewrote rate(): merge the first, never the second"
"$here/rex-crew" integrate "$repo" alpha beta --verify 'python3 test_f.py' > "$tmp/i.out"; rc=$?; cat "$tmp/i.out"
[ $rc -eq 1 ] || fail "integrate should stop at the conflict, got $rc"
grep -q 'crew/alpha: merged' "$tmp/i.out" || fail "alpha merges"
grep -q 'crew/beta: conflict: f.py' "$tmp/i.out" || fail "beta conflicts"
test -z "$(git -C "$repo" status --porcelain)" || fail "the tree must be clean after a conflict"
# carry-over task: beta's diff, the current state, its tests unchanged
git -C "$repo" diff main...crew/beta > "$tmp/beta.diff"
cat "$tmp/beta.diff" > /dev/null
echo "Carry the change of branch crew/beta (diff attached) over to the current state of main. Do not edit test_f.py. Run python3 test_f.py; commit only when green." > "$tmp/carry.md"
c=$("$here/rex-crew" checkout carry "$repo") || fail "checkout carry"
"$here/rex-crew" start carry "$c" "$tmp/carry.md" --verify 'python3 test_f.py' --trust -- --permission-mode acceptEdits >/dev/null || fail "start carry"
( cd "$c" && printf 'def rate():\n    return 3\n' > f.py && git add -A && git commit -qm "carry beta" \
  && mkdir -p .rex-crew && printf 'STATUS: done\nCHANGED: f.py\nCOMMIT: x\n' > .rex-crew/report-carry.md )
"$here/rex-crew" collect carry --timeout 8 > "$tmp/c3.out" || fail "collect carry"; cat "$tmp/c3.out"
grep -q 'verify: exit 0' "$tmp/c3.out" || fail "carry verified"
"$here/rex-crew" integrate "$repo" carry --verify 'python3 test_f.py' || fail "integrate carry"
grep -q 'return 3' "$repo/f.py" || fail "beta's work is on main"
git -C "$repo" diff --quiet HEAD~1 -- test_f.py || fail "the tests changed"
echo "tally: (b) carried over, (d) looked at the screen, (f) verify said when absent"
