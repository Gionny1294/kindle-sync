#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
sh scripts/start.sh "$@"
result=$?
if [ "$result" -ne 0 ]; then
  printf '\nPress Enter to close. '
  read -r answer
fi
exit "$result"
