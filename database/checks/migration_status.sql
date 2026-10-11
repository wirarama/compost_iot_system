-- Which migrations are in place? Read-only; safe to run at any time.
--
--   psql "$PGURL" -f database/checks/migration_status.sql
--
-- One row per thing a migration adds. Add rows here with every new migration.

WITH fk AS (
    -- every single-column foreign key, with its ON DELETE rule
    SELECT con.conrelid::regclass::text AS tbl,
           att.attname::text            AS col,
           CASE con.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' ELSE 'NO ACTION' END AS on_delete
    FROM pg_constraint con
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
    WHERE con.contype = 'f' AND array_length(con.conkey, 1) = 1
),
checks (migration, item, ok) AS (
    VALUES
        ('001', 'table users exists',
            to_regclass('public.users') IS NOT NULL),
        ('001', 'table records exists',
            to_regclass('public.records') IS NOT NULL),

        ('002', 'users.created_at',
            EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'created_at')),
        ('002', 'users.display_name',
            EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'display_name')),
        ('002', 'unique index on lower(email)',
            to_regclass('public.users_email_lower_key') IS NOT NULL),
        ('002', 'bins.user_id ON DELETE CASCADE',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'bins' AND col = 'user_id' AND on_delete = 'CASCADE')),
        ('002', 'setup_codes.user_id ON DELETE CASCADE',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'setup_codes' AND col = 'user_id' AND on_delete = 'CASCADE')),
        ('002', 'devices.owner_id ON DELETE SET NULL',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'devices' AND col = 'owner_id' AND on_delete = 'SET NULL')),
        ('002', 'device_bin_assn.bin_id ON DELETE CASCADE',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'device_bin_assn' AND col = 'bin_id' AND on_delete = 'CASCADE')),
        ('002', 'records.bin_id ON DELETE CASCADE',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'records' AND col = 'bin_id' AND on_delete = 'CASCADE')),
        ('002', 'bin_events.bin_id ON DELETE CASCADE',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'bin_events' AND col = 'bin_id' AND on_delete = 'CASCADE')),
        ('002', 'bin_events.reading_id ON DELETE SET NULL',
            EXISTS (SELECT 1 FROM fk WHERE tbl = 'bin_events' AND col = 'reading_id' AND on_delete = 'SET NULL')),

        ('003', 'devices.name, model, firmware_version, paired_at, last_seen_at',
            (SELECT count(*) = 5 FROM information_schema.columns WHERE table_name = 'devices'
             AND column_name IN ('name', 'model', 'firmware_version', 'paired_at', 'last_seen_at'))),
        ('003', 'unique index on upper(devices.mac)',
            to_regclass('public.devices_mac_key') IS NOT NULL),
        ('003', 'unique index on device_apikeys.api_key_hash',
            to_regclass('public.device_apikeys_hash_key') IS NOT NULL),
        ('003', 'one open bin assignment per device',
            to_regclass('public.device_bin_assn_open_key') IS NOT NULL),
        ('003', 'bins.country_code',
            EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'bins' AND column_name = 'country_code')),
        ('003', 'setup_codes.id is the primary key',
            EXISTS (SELECT 1 FROM information_schema.key_column_usage k
                    JOIN information_schema.table_constraints t USING (constraint_name, table_name)
                    WHERE t.table_name = 'setup_codes' AND t.constraint_type = 'PRIMARY KEY' AND k.column_name = 'id')),
        ('003', 'unique index on live setup codes',
            to_regclass('public.setup_codes_live_code_key') IS NOT NULL),
        ('003', 'table pairing_failures exists',
            to_regclass('public.pairing_failures') IS NOT NULL),

        ('004', 'devices.mac is optional',
            EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'devices' AND column_name = 'mac' AND is_nullable = 'YES')),

        ('004', 'devices.registration, not null',
            EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'devices' AND column_name = 'registration' AND is_nullable = 'NO')),
        ('004', 'registration is pairing or manual, and manual exactly when there is no MAC',
            (SELECT count(*) = 2 FROM pg_constraint
             WHERE conname IN ('devices_registration_check', 'devices_registration_mac_check')))
)
SELECT migration, item, CASE WHEN ok THEN 'applied' ELSE 'MISSING' END AS status
FROM checks
ORDER BY migration, item;
