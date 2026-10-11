# CompostIQ database

PostgreSQL 16. Only the API (`backendAPI/`) connects to it. The dashboard and the simulator never do; they go through the API.

## What is in this folder

| File | What it is | Where it runs |
|---|---|---|
| `CompostIQ_PostgreSQL_schema_fix1.sql` | The base schema: all 9 tables. Treated as migration **001**. Never edited | everywhere, once |
| `migrations/NNN_name.sql` | Changes on top of the base schema, applied in number order | everywhere |
| `apply_migrations.sh` | Applies every file in `migrations/` when the local Compose database is first created | local only |
| `seed_dev.sql` | One development user, bin and device with fixed ids. The user has a **published password** | **local only, never the VM** |
| `dummy_records.sql` | Old sample data. Superseded by `seed_dev.sql`; it cannot be loaded after the schema | not used |

## Migrations

| # | File | What it changes | Touches existing data? |
|---|---|---|---|
| 001 | `CompostIQ_PostgreSQL_schema_fix1.sql` | Creates the tables | no |
| 002 | `migrations/002_users_auth.sql` | `users.created_at`, `users.display_name`; emails unique without regard to case; `ON DELETE` rules for account deletion | yes: lower-cases `users.email` |
| 003 | `migrations/003_device_pairing.sql` | Device pairing: `devices.name`, `model`, `firmware_version`, `paired_at`, `last_seen_at`; one row per hardware ID; `bins.country_code`; `setup_codes` gets its own `id` primary key so a code number can be reused; new `pairing_failures` table; unique indexes on key hashes and on a device's open bin assignment | yes: gives existing `setup_codes` rows an `id`. Fails if two devices share a MAC (any case) or a device has two open bin assignments; fix those by hand first |
| 004 | `migrations/004_device_registration.sql` | Devices registered without pairing: `devices.mac` becomes optional, since nothing is learnt from the hardware; `devices.registration` (`pairing` or `manual`) records how a device joined, and checks keep it `manual` exactly when there is no MAC. Only `manual` devices may get a new API key | yes: fills in `registration` for existing devices from `mac` |

Accounts that existed before 002 get the time the migration ran as their `created_at`, because their real sign-up time was never recorded.

There is no table that records which migrations have run. Every migration is written so it can be **run again safely**, so the rule is simple: when in doubt, run them all, in order. Each one also has a check query below that tells you whether it is in place.

## Playbook: local development

**Start fresh** (wipes your local data, then loads schema → migrations → seed):

```bash
docker compose down -v
```

```bash
docker compose up -d
```

**Keep your data** and apply one new migration:

```bash
docker compose exec -T db psql -U compostiq -d compostiq -v ON_ERROR_STOP=1 < database/migrations/002_users_auth.sql
```

The Compose init scripts only run when the volume is empty. After pulling a new migration, use one of the two commands above; `docker compose up -d` by itself will not apply it.

## Playbook: the VM (production)

Run these on the VM, from the repo root (`~/compost_iot_system`), after `git pull`. Every step is a separate command so you can stop if one fails.

**Before you start**

- `psql` needs the plain form of the URL: `postgresql://…`, not `postgresql+psycopg2://…`.
- Connect as the role that owns the tables (`compostiq`). Changing a table needs its owner.
- Set the URL once for this shell. Typing it with a leading space keeps the password out of your shell history:

```bash
 export PGURL='postgresql://compostiq:CHANGE_ME@127.0.0.1:5432/compostiq'
```

### 1. See what is already applied

```bash
psql "$PGURL" -f database/checks/migration_status.sql
```

Each row says `applied` or `MISSING`. If everything is `applied`, stop here.

### 2. Check the data will migrate cleanly

Migration 002 makes emails unique without regard to case. Two accounts that differ only by case would make it fail, so look first:

```bash
psql "$PGURL" -c "SELECT lower(email) AS email, count(*) FROM users GROUP BY 1 HAVING count(*) > 1;"
```

Expect `(0 rows)`. If any rows come back, decide which account to keep and delete or rename the other before going on.

### 3. Back up

```bash
pg_dump "$PGURL" --format=custom --file="$HOME/compostiq-$(date +%F-%H%M).dump"
```

```bash
ls -lh ~/compostiq-*.dump
```

Do not continue unless the file exists and is not empty. This file is your rollback.

### 4. Stop the API

```bash
pm2 stop compostiq-api
```

The migration changes table definitions, which needs a brief exclusive lock. Stopping the API means no device upload is caught half-way. Devices get an error for a minute and send again later.

### 5. Apply the migrations

```bash
for f in database/migrations/*.sql; do echo "== $f"; psql "$PGURL" -v ON_ERROR_STOP=1 -f "$f" || break; done
```

- Each migration runs inside one transaction. If it fails, **nothing from that file is applied** and the loop stops.
- `NOTICE: … already exists, skipping` is normal when a migration has run before.
- Any line starting with `ERROR` means it did not apply. Go to "If something goes wrong".

### 6. Verify

```bash
psql "$PGURL" -f database/checks/migration_status.sql
```

Every row should now say `applied`.

### 7. Start the API and check it

Migration 002 arrives with the sign-in release, which needs new settings. Make sure `.env` has `JWT_SECRET`, `API_URL` and `DASHBOARD_SECRET_KEY` first (see [`deploy/pm2/README.md`](../deploy/pm2/README.md)).

```bash
pm2 reload deploy/pm2/ecosystem.config.js --update-env
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/auth/me
```

Expect `401`: the API is up, the auth routes exist, and it is refusing a request with no token. `000` or `502` means the API did not start; read `pm2 logs compostiq-api`.

### If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `ERROR` during step 5 | That migration rolled itself back. The database is as it was before that file | Fix the cause, run step 5 again. Start the API again if you need it up meanwhile: the old code works with the old schema |
| `duplicate key value violates unique constraint "users_email_key"` | Two accounts share an email apart from case | Step 2, then step 5 again |
| `must be owner of table …` | Connected as the wrong role | Use the `compostiq` role in `PGURL` |
| The migration applied but the app misbehaves | | Restore the backup (below) |

**Restoring the backup** puts the database back exactly as it was at step 3. Anything written since then is lost, so stop the API first.

```bash
pm2 stop compostiq-api
```

```bash
pg_restore --dbname="$PGURL" --clean --if-exists --no-owner --single-transaction ~/compostiq-YYYY-MM-DD-HHMM.dump
```

Then check out the previous release of the code and start the API again.

This playbook was rehearsed on 2026-09-29 against a copy of the base schema holding a user, a bin, a device and 500 readings: the clean path, the duplicate-email failure (nothing applied), and the restore.

## Writing a new migration

1. **Name it** with the next number: `migrations/004_what_it_does.sql`. Files run in name order.
2. **Never edit a migration that has been applied anywhere.** Write a new one that changes what the old one did.
3. **Wrap it in `BEGIN;` … `COMMIT;`** so it applies completely or not at all.
4. **Make it safe to run twice**: `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`. For anything without an `IF NOT EXISTS` form, check first inside a `DO $$ … $$` block (002 does this for foreign keys).
5. **Add its check** to `checks/migration_status.sql` and a row to the table at the top of this file.
6. **Test both paths locally**: a fresh start (`docker compose down -v && docker compose up -d`), and applying it on top of a database that already holds data.
7. If `seed_dev.sql` needs the new columns, update it in the same commit. It loads after the migrations.

## The development seed

`seed_dev.sql` gives the local database one user, bin, device and assignment, with fixed ids the tests rely on.

| | |
|---|---|
| User | `dev@compostiq.local` |
| Password | in the comment at the top of `seed_dev.sql` |
| Device API key | none. Tests create their own |

The password is published in this repository, so that user must never exist on the VM. To confirm the VM is clean:

```bash
psql "$PGURL" -c "SELECT count(*) FROM users WHERE email = 'dev@compostiq.local';"
```

Expect `0`.
