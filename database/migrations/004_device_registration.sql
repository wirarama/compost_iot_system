-- 004_device_registration.sql - devices registered without pairing.
--
-- Besides pairing (003), a device can join an account straight from the
-- dashboard: POST /devices names it, puts it in a bin and returns its API key
-- once, and the user copies the key onto the device by hand. Nothing is
-- learnt from the hardware, so such a device has no MAC address. The unique
-- index on upper(mac) from 003 already lets any number of devices have none.
--
-- devices.registration records how each device joined: 'pairing' (it
-- redeemed a code and reported its hardware ID) or 'manual' (registered in
-- the dashboard). Only a manual device may swap its key for a new one
-- (POST /devices/{id}/key); a paired device gets a new key by being paired
-- again. It is set when the device is created, and checked: a paired device
-- always has a MAC and a registered one never does, so a paired device can't
-- be passed off as a registered one, whatever writes the row.
--
-- Applies on top of 003_device_pairing.sql. Safe to run more than once,
-- including on a database that ran an earlier version of this file.
--   local:  docker compose exec -T db psql -U compostiq -d compostiq < database/migrations/004_device_registration.sql
--   VM:     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/migrations/004_device_registration.sql

BEGIN;

ALTER TABLE devices ALTER COLUMN mac DROP NOT NULL;

ALTER TABLE devices ADD COLUMN IF NOT EXISTS registration text;
UPDATE devices SET registration = CASE WHEN mac IS NULL THEN 'manual' ELSE 'pairing' END
WHERE registration IS NULL;
-- pairing, the seed and older tests insert devices without naming it; they all have a MAC
ALTER TABLE devices ALTER COLUMN registration SET DEFAULT 'pairing';
ALTER TABLE devices ALTER COLUMN registration SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'devices_registration_check') THEN
        ALTER TABLE devices ADD CONSTRAINT devices_registration_check
            CHECK (registration IN ('pairing', 'manual'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'devices_registration_mac_check') THEN
        ALTER TABLE devices ADD CONSTRAINT devices_registration_mac_check
            CHECK ((registration = 'manual') = (mac IS NULL));
    END IF;
END
$$;

COMMIT;
