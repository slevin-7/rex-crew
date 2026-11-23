#!/bin/sh
# Links the rex-crew CLI and skill. Nothing is copied; --uninstall removes only links that point here.
set -eu
root=$(cd -- "$(dirname -- "$0")" && pwd -P)
bin="${REX_CREW_BIN_DIR:-$HOME/.local/bin}/rex-crew"
skill="${REX_CREW_SKILLS_DIR:-$HOME/.claude/skills}/rex-crew"
link() {
  if [ -L "$2" ] && [ "$(readlink "$2")" = "$1" ]; then echo "ok      $2"; return; fi
  [ ! -e "$2" ] && [ ! -L "$2" ] || { echo "install.sh: $2 exists; remove it first" >&2; exit 1; }
  mkdir -p "$(dirname "$2")"; ln -s "$1" "$2"; echo "linked  $2 -> $1"
}
unlink_ours() { if [ -L "$2" ] && [ "$(readlink "$2")" = "$1" ]; then rm "$2"; echo "removed $2"; fi; }
if [ "${1:-}" = "--uninstall" ]; then
  unlink_ours "$root/skills/rex-crew/scripts/rex-crew" "$bin"; unlink_ours "$root/skills/rex-crew" "$skill"
else
  link "$root/skills/rex-crew/scripts/rex-crew" "$bin"; link "$root/skills/rex-crew" "$skill"
fi
