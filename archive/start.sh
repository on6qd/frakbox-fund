#!/bin/bash
cd "$(dirname "$0")"

if pgrep -f "researcher.sh" >/dev/null; then
  echo "Already running (pid $(pgrep -f researcher.sh))"
  exit 1
fi

nohup ./researcher.sh > /dev/null 2>&1 &
echo "Started (pid $!)"
