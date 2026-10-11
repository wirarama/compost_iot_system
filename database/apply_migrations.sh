#!/bin/sh
# Runs inside the Compose database container on its first start, after the
# schema and before the dev seed. Applies every file in database/migrations/
# in name order, so a new migration needs no change to compose.yaml.
set -e

for file in /migrations/*.sql; do
    echo "apply_migrations: $file"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$file"
done
