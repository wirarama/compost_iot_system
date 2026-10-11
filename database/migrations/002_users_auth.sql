-- 002_users_auth.sql - what user sign-in and account deletion need.
--
-- Applies on top of CompostIQ_PostgreSQL_schema_fix1.sql (treated as 001).
-- Safe to run more than once.
--
--   local:  loaded by compose.yaml on the first start (docker compose down -v
--           && docker compose up -d reloads everything)
--   VM:     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/migrations/002_users_auth.sql
--           (use the plain postgresql:// form of the URL, without +psycopg2)

BEGIN;

-- ------------------------------------------------------------------ users ---

ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name text;

-- The API stores emails in lower case. Bring older rows into line first, so
-- the index below cannot be created over two spellings of the same address
-- (if two such rows exist this fails, and they need merging by hand).
UPDATE users SET email = lower(email) WHERE email <> lower(email);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key ON users (lower(email));

-- --------------------------------------------------------- ON DELETE rules ---
-- DELETE /auth/me removes the user and their bins; everything hanging off a
-- bin goes with it. Devices are hardware, so they are never deleted with the
-- account - they lose their owner and can be paired again.
--
--   users -> bins              CASCADE
--   users -> setup_codes       CASCADE
--   users -> devices           SET NULL  (the API also deactivates the device
--                                         and revokes its keys)
--   bins  -> device_bin_assn   CASCADE
--   bins  -> records           CASCADE
--   bins  -> bin_events        CASCADE
--   records -> bin_events      SET NULL  (reading_id is optional context)
--
-- The schema created its foreign keys without names, so each one is looked up
-- by table and column rather than by a guessed constraint name.

DO $$
DECLARE
    rule record;
    old_name text;
BEGIN
    FOR rule IN
        SELECT * FROM (VALUES
            ('bins',            'user_id',    'users',   'CASCADE'),
            ('setup_codes',     'user_id',    'users',   'CASCADE'),
            ('devices',         'owner_id',   'users',   'SET NULL'),
            ('device_bin_assn', 'bin_id',     'bins',    'CASCADE'),
            ('records',         'bin_id',     'bins',    'CASCADE'),
            ('bin_events',      'bin_id',     'bins',    'CASCADE'),
            ('bin_events',      'reading_id', 'records', 'SET NULL')
        ) AS r (tbl, col, ref_tbl, action)
    LOOP
        FOR old_name IN
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
            WHERE con.contype = 'f'
              AND con.conrelid = rule.tbl::regclass
              AND con.confrelid = rule.ref_tbl::regclass
              AND array_length(con.conkey, 1) = 1
              AND att.attname = rule.col
        LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', rule.tbl, old_name);
        END LOOP;

        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I (id) '
            'ON DELETE %s DEFERRABLE INITIALLY IMMEDIATE',
            rule.tbl, rule.tbl || '_' || rule.col || '_fkey', rule.col, rule.ref_tbl, rule.action
        );
    END LOOP;
END
$$;

COMMIT;
