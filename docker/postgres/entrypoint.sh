#!/bin/sh
set -eu
PGDATA="${PGDATA:-/var/lib/postgresql/data}"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  /opt/postgresql/bin/initdb -D "$PGDATA" --auth-local=trust --auth-host=trust
  printf '\n# B1 test-only internal Docker network access; no host port publication.\nhost all all samenet trust\n' >> "$PGDATA/pg_hba.conf"
fi
exec /opt/postgresql/bin/postgres -D "$PGDATA" -c listen_addresses='*'
