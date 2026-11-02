#!/usr/bin/env bash
# Migrate/provision the existing one-issuer/one-client development iteration.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
"$BENCH/env/bin/python" "$ROOT/env/deployment.py" framework --bench "$BENCH"
[ -f "$BENCH/sites/id.localhost/site_config.json" ]
[ -f "$BENCH/sites/site1.localhost/site_config.json" ]
"$BENCH/env/bin/pip" install --no-deps -e "$ROOT/apps/passport_core"
"$BENCH/env/bin/python" "$ROOT/env/provision_iteration.py"
cd "$BENCH"
bench --site id.localhost migrate --skip-search-index
bench --site site1.localhost migrate --skip-search-index
bench --site id.localhost execute passport.setup.provision_iteration
"$BENCH/env/bin/python" "$ROOT/env/deployment.py" build
