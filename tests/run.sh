#!/bin/sh
# Runs every test file at once, one process each, and prints a tally. Same tests as
# `python3 -m unittest discover -s tests`, in a fraction of the time. REX_CREW_JOBS sets how many at once (default 8).
cd "$(dirname "$0")/.." || exit 1
out=$(mktemp -d "${TMPDIR:-/tmp}/rex-crew-tests.XXXXXX") || exit 1
trap 'rm -rf "$out"' EXIT
ls tests/test_*.py | xargs -P "${REX_CREW_JOBS:-8}" -I{} sh -c 'python3 -m unittest "$1" >"$2/$(basename "$1").log" 2>&1; echo $? >"$2/$(basename "$1").rc"' _ {} "$out"
bad=0; total=0
for f in tests/test_*.py; do
  b=$(basename "$f"); n=$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$out/$b.log" | tail -1); total=$((total + ${n:-0}))
  if [ "$(cat "$out/$b.rc" 2>/dev/null)" != 0 ]; then bad=$((bad + 1)); echo "FAILED $f"; awk '/^(FAIL|ERROR):/{p=1} p; /^Ran /{p=0}' "$out/$b.log" | head -80; tail -3 "$out/$b.log"; fi
done
[ "$bad" = 0 ] && echo "OK: $total tests in $(ls tests/test_*.py | wc -l | tr -d ' ') files" || { echo "$bad file(s) failed, $total tests ran"; exit 1; }
