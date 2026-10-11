-- Minimal development seed, loaded by compose.yaml after the schema and the
-- migrations. Fixed UUIDs so tests and local tools can refer to these rows.
--
-- DEV ONLY - never load this file into the VM's database.
--   * the dev user signs in with  dev@compostiq.local / compostiq-dev
--     (an argon2id hash of that documented, local-only password)
--   * no device API key is seeded: keys are created per test / per developer,
--     never committed
--
-- Replaces database/dummy_records.sql for local use (that file re-creates the
-- records table and cannot be loaded after the schema).

INSERT INTO users (id, email, display_name, password_hash)
VALUES ('00000000-0000-4000-8000-000000000001', 'dev@compostiq.local', 'Dev User',
        '$argon2id$v=19$m=65536,t=3,p=4$14twJeAF446ckEBLWZ8P0w$hda8l/9WrH/mMKsHT1aUE08eFUUyBgyfSY72ugcxlU8');

INSERT INTO bins (id, location, user_id, name)
VALUES ('00000000-0000-4000-8000-000000000101', 'Mataram, Lombok, Indonesia',
        '00000000-0000-4000-8000-000000000001', 'UNRAM Bin 1');

-- paired, named and in a bin: set up, as if it had gone through pairing
INSERT INTO devices (id, mac, owner_id, is_active, name, model, paired_at)
VALUES ('00000000-0000-4000-8000-000000000201', '02:00:00:00:00:01',
        '00000000-0000-4000-8000-000000000001', true, 'Seed sensor', 'ESP32-C3', now());

INSERT INTO device_bin_assn (device_id, bin_id)
VALUES ('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101');
