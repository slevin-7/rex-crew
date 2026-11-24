#!/bin/sh
# Orchestration walkthrough written from skills/rex-crew/SKILL.md alone.
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
here="$root/skills/rex-crew/scripts"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export REX_CREW_STATE="$tmp/runs.json"
# fake host: a rex on PATH that is tests/fake_rex, with one scripted status sequence per worker label
mkdir "$tmp/bin" && ln -s "$root/tests/fake_rex" "$tmp/bin/rex"
export PATH="$tmp/bin:$PATH" FAKE_REX_STATE="$tmp/fake.json"
unset REX_BLOCK REX_CREW_HOST REX_SESSION
python3 - "$FAKE_REX_STATE" <<'PY'
import json, sys
w, b = {"state": "working"}, lambda kind, msg: {"state": "blocked", "kind": kind, "msg": msg}
scripts = {"one": [w, {"state": "done"}],
           "two": [w, *[b("permission", "approve Bash: make deploy")] * 4, w, {"state": "done"}],
           "three": [w, *[b("question", "Which database? 1. staging 2. prod")] * 4, w, {"state": "done"}]}
json.dump({"next": 1, "sessions": [], "scripts": scripts, "blocks": {}, "sent": []}, open(sys.argv[1], "w"))
PY

# repo with three files
repo="$tmp/repo"
mkdir "$repo" && cd "$repo" || exit 1
git init -q -b main . && git config user.email t@t && git config user.name t
echo base > a.txt; echo base > b.txt; echo base > c.txt
git add . && git commit -qm base

cat > "$tmp/one.md" <<'EOF'
Write a.txt with the word one and commit it.
EOF
cat > "$tmp/two.md" <<'EOF'
Write b.txt with the word two and commit it.
EOF
cat > "$tmp/three.md" <<'EOF'
Write c.txt with the word three and commit it.
EOF
cat > "$tmp/policy.json" <<'EOF'
{ "approve": ["^Bash: git (status|log|diff)(\\s|$)"], "never": ["git push"] }
EOF
cat > "$tmp/plan.json" <<EOF
{ "repo": "$repo", "base": "main", "trust": true,
  "claude_args": ["--permission-mode", "acceptEdits"],
  "workers": [ {"name": "one", "task_file": "one.md"},
               {"name": "two", "task_file": "two.md"},
               {"name": "three", "task_file": "three.md"} ] }
EOF

export REX_CREW_SESSION=$("$here/rex-crew" session walkthrough) || exit 1
started=$("$here/rex-crew" fanout "$tmp/plan.json") || exit 1
echo "$started"
# the workers (played by this script) commit their file and write their report in their checkout
echo "$started" | grep '^started ' | while read -r _ name _ _ _ dir _; do
  ( cd "$dir" && echo "$name" > "$name.txt" \
    && git add -A && git commit -qm "work of $name" \
    && mkdir -p .rex-crew && printf 'STATUS: done\nCHANGED: %s\nCOMMIT: x\n' "$name" > ".rex-crew/report-$name.md" )
done

# collect until nothing needs us; answer every worker that does
n=0
while :; do
  out=$("$here/rex-crew" collect --policy "$tmp/policy.json" --timeout 60); rc=$?
  echo "$out"
  [ $rc -eq 0 ] && break
  [ $rc -eq 2 ] || { echo "collect exit $rc"; exit 1; }
  echo "$out" | awk '/^== .*state: blocked/ {for(i=1;i<=NF;i++) if($i=="block:") b=$(i+1)}
                     /^needs you \(/ {k=$3; gsub(/[():]/,"",k); print b, k}' |
  while read -r block kind; do
    case $kind in
      permission) "$here/rex-crew" approve "$block" ;;
      question)   "$here/rex-crew" answer "$block" 1 ;;
    esac
  done
  n=$((n+1)); [ $n -lt 10 ] || { echo "too many rounds"; exit 1; }
done

"$here/rex-crew" integrate "$repo" one two three --verify 'test -f a.txt' || exit 1
for f in one two three; do test -f "$repo/$f.txt" || { echo "missing $f.txt"; exit 1; }; done
echo "tally: 3 workers collected and integrated, $(git -C "$repo" log --oneline | wc -l | tr -d ' ') commits on main"
python3 - "$FAKE_REX_STATE" <<'PY'
import json, sys
sent = json.load(open(sys.argv[1]))["sent"]
print("tally: keys sent to panes:", ", ".join(f"{s['block']}={s['value']}" for s in sent))
PY
