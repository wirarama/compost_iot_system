-- 003_device_pairing.sql - what pairing a device needs.
--
-- The flow (emulator/README.md, "Pairing"):
--   1. the dashboard asks for a code       POST /pairing/codes      (user JWT)
--   2. the device redeems it for a key     POST /pairing/redeem     (the code)
--   3. the user confirms and sets it up    POST /devices/{id}/setup (user JWT)
-- Until step 3 the device has an owner and a key but no bin, so /records
-- answers 409 and the device waits.
--
-- Applies on top of 002_users_auth.sql. Safe to run more than once.
--   local:  docker compose exec -T db psql -U compostiq -d compostiq < database/migrations/003_device_pairing.sql
--   VM:     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/migrations/003_device_pairing.sql

BEGIN;

-- ---------------------------------------------------------------- devices ---

ALTER TABLE devices ADD COLUMN IF NOT EXISTS name text;
-- what the device reported about itself when it was paired
ALTER TABLE devices ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS firmware_version text;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS paired_at timestamptz;
-- set by /records, so the dashboard can tell the device is online
ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

-- one row per piece of hardware. Case-insensitive, because MACs are written
-- both ways; the API stores them upper case.
CREATE UNIQUE INDEX IF NOT EXISTS devices_mac_key ON devices (upper(mac));
CREATE INDEX IF NOT EXISTS devices_owner_id_idx ON devices (owner_id);

-- a key is looked up by its hash on every /records call (audit A9)
CREATE UNIQUE INDEX IF NOT EXISTS device_apikeys_hash_key ON device_apikeys (api_key_hash);

-- a device sits in at most one bin at a time (audit A10)
CREATE UNIQUE INDEX IF NOT EXISTS device_bin_assn_open_key
    ON device_bin_assn (device_id) WHERE unassigned_at IS NULL;

-- ------------------------------------------------------------------- bins ---

-- ISO 3166-1 alpha-2, e.g. 'ID'. Nullable for bins made before this migration.
ALTER TABLE bins ADD COLUMN IF NOT EXISTS country_code char(2);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bins_country_code_check') THEN
        ALTER TABLE bins ADD CONSTRAINT bins_country_code_check CHECK (country_code ~ '^[A-Z]{2}$');
    END IF;
END
$$;
CREATE INDEX IF NOT EXISTS bins_user_id_idx ON bins (user_id);

-- ------------------------------------------------------------ setup_codes ---
-- The schema made `code` the primary key, so a code could never be issued
-- twice. Give rows their own id instead, and only keep live codes unique.

ALTER TABLE setup_codes ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid();
UPDATE setup_codes SET id = gen_random_uuid() WHERE id IS NULL;
ALTER TABLE setup_codes ALTER COLUMN id SET NOT NULL;
ALTER TABLE setup_codes ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();

DO $$
DECLARE
    pk_columns text[];
BEGIN
    SELECT array_agg(att.attname::text) INTO pk_columns
    FROM pg_constraint con
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    WHERE con.conrelid = 'setup_codes'::regclass AND con.contype = 'p';

    IF pk_columns IS DISTINCT FROM ARRAY['id'] THEN
        IF pk_columns IS NOT NULL THEN
            EXECUTE (SELECT format('ALTER TABLE setup_codes DROP CONSTRAINT %I', conname)
                     FROM pg_constraint WHERE conrelid = 'setup_codes'::regclass AND contype = 'p');
        END IF;
        ALTER TABLE setup_codes ADD PRIMARY KEY (id);
    END IF;
END
$$;

-- expired, never-used codes are deleted when new ones are issued, so this
-- only ever covers codes that could still be redeemed
CREATE UNIQUE INDEX IF NOT EXISTS setup_codes_live_code_key ON setup_codes (code) WHERE used_at IS NULL;
CREATE INDEX IF NOT EXISTS setup_codes_user_id_idx ON setup_codes (user_id);

-- ------------------------------------------------------- pairing_failures ---
-- Wrong codes sent to POST /pairing/redeem, per client IP. Kept in the
-- database rather than in memory because the API runs several workers.
-- Rows older than the window are deleted as new ones arrive.

CREATE TABLE IF NOT EXISTS pairing_failures (
    id bigserial PRIMARY KEY,
    client_ip text NOT NULL,
    failed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pairing_failures_ip_idx ON pairing_failures (client_ip, failed_at);

COMMIT;
