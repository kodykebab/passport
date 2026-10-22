# shellcheck shell=bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH="$ROOT/.bench"
DATA="$ROOT/.data"
MARIADB=/opt/homebrew/opt/mariadb@11.8
export PATH="/opt/homebrew/opt/node@26/bin:$MARIADB/bin:$PATH"

DB_PORT=3327
DB_ROOT_PASSWORD=root
WEB_PORT=8080
ISSUER=id.localhost
SITES=(site1.localhost site2.localhost)
ADMIN_PASSWORD=admin
DEMO_USER=alice@example.com
DEMO_PASSWORD=alice-passport-1

mkdir -p "$DATA/logs"

mariadb_up() {
	if "$MARIADB/bin/mariadb-admin" -uroot -p"$DB_ROOT_PASSWORD" -h127.0.0.1 -P$DB_PORT ping >/dev/null 2>&1; then
		return
	fi
	if [ ! -d "$DATA/mariadb" ]; then
		"$MARIADB/bin/mariadb-install-db" --datadir="$DATA/mariadb" --auth-root-authentication-method=normal \
			--basedir="$MARIADB" >"$DATA/logs/mariadb-init.log" 2>&1
		local fresh=1
	fi
	"$MARIADB/bin/mariadbd" --no-defaults --datadir="$DATA/mariadb" --port=$DB_PORT --bind-address=127.0.0.1 \
		--socket="$DATA/mariadb.sock" --pid-file="$DATA/mariadb.pid" --skip-log-bin \
		--character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >>"$DATA/logs/mariadb.log" 2>&1 &
	for _ in $(seq 30); do
		"$MARIADB/bin/mariadb-admin" -uroot -h127.0.0.1 -P$DB_PORT ping >/dev/null 2>&1 && break
		"$MARIADB/bin/mariadb-admin" -uroot -p"$DB_ROOT_PASSWORD" -h127.0.0.1 -P$DB_PORT ping >/dev/null 2>&1 && break
		sleep 1
	done
	if [ -n "${fresh:-}" ]; then
		"$MARIADB/bin/mariadb" -uroot -h127.0.0.1 -P$DB_PORT -e "
			ALTER USER 'root'@'localhost' IDENTIFIED BY '$DB_ROOT_PASSWORD';
			CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '$DB_ROOT_PASSWORD';
			GRANT ALL ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
			DROP USER IF EXISTS ''@'localhost'; FLUSH PRIVILEGES;"
	fi
}
