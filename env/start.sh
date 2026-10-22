#!/usr/bin/env bash
# Starts MariaDB and the bench (web, redis, workers) in the foreground.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
mariadb_up
cd "$BENCH"
exec bench start
