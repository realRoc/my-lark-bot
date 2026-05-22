#!/usr/bin/env bash
set -euo pipefail

label="com.teamo.feishu-bugbot"
uid="$(id -u)"
launchctl print "gui/${uid}/${label}" | sed -n '1,80p'
echo
echo "--- recent log ---"
tail -n 80 "$(dirname "$0")/../.state/launchd.err.log" 2>/dev/null || true
