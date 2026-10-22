#!/usr/bin/env bash
# Migrate/provision the existing one-issuer/one-client development iteration.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[ "$(git -C "$BENCH/apps/frappe" rev-parse HEAD)" = 4dc04fac4dd5bf20feb9f24e029202b3eadb52b8 ] || { echo 'Unexpected framework baseline'; exit 1; }
[ -f "$BENCH/sites/id.localhost/site_config.json" ]
[ -f "$BENCH/sites/site1.localhost/site_config.json" ]
"$BENCH/env/bin/pip" install --no-deps -e "$ROOT/apps/passport_core"
"$BENCH/env/bin/python" "$ROOT/env/provision_iteration.py"
cd "$BENCH"
bench --site id.localhost migrate --skip-search-index
bench --site site1.localhost migrate --skip-search-index
bench --site id.localhost execute passport.setup.provision_iteration
bench build --app passport_client
