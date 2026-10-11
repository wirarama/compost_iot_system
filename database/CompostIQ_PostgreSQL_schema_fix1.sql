CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE "users" (
  "id" uuid PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
  "email" text UNIQUE NOT NULL,
  "password_hash" text NOT NULL
);

CREATE TABLE "bins" (
  "id" uuid PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
  "location" text NOT NULL,
  "user_id" uuid NOT NULL,
  "name" text,
  "created_at" timestamptz DEFAULT (now())
);

CREATE TABLE "devices" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "mac" text NOT NULL,
  "owner_id" uuid,
  "created_at" timestamptz DEFAULT (now()),
  "is_active" boolean NOT NULL DEFAULT true
);

CREATE TABLE "device_apikeys" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "device_id" uuid NOT NULL,
  "api_key_hash" text NOT NULL,
  "issued_at" timestamptz DEFAULT (now()),
  "revoked_at" timestamptz
);

CREATE TABLE "setup_codes" (
  "code" text PRIMARY KEY,
  "user_id" uuid NOT NULL,
  "device_id" uuid,
  "expiry" timestamptz NOT NULL,
  "used_at" timestamptz
);

CREATE TABLE "device_bin_assn" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "device_id" uuid NOT NULL,
  "bin_id" uuid NOT NULL,
  "assigned_at" timestamptz NOT NULL DEFAULT (now()),
  "unassigned_at" timestamptz
);

CREATE TABLE "records" (
  "id" bigserial PRIMARY KEY,
  "device_id" uuid NOT NULL,
  "bin_id" uuid NOT NULL,
  "timestamp" timestamptz NOT NULL,
  "received_at" timestamptz NOT NULL DEFAULT (now()),
  "temperature" double precision NOT NULL CHECK ("temperature" BETWEEN 0 AND 100),
  "moisture_percent" double precision NOT NULL CHECK ("moisture_percent" BETWEEN 0 AND 100),
  "o2_percent" double precision NOT NULL CHECK ("o2_percent" BETWEEN 0 AND 25),
  "co2_percent" double precision NOT NULL CHECK ("co2_percent" BETWEEN 0 AND 15),
  "nh3_ratio" double precision NOT NULL CHECK ("nh3_ratio" BETWEEN 0 AND 1)
);

CREATE TABLE "device_maintenance" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "device_id" uuid NOT NULL,
  "scheduled_for" timestamptz,
  "completed_at" timestamptz,
  "type" text,
  "notes" text
);

CREATE TABLE "bin_events" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "bin_id" uuid NOT NULL,
  "type" text NOT NULL,
  "severity" text NOT NULL,
  "triggered_at" timestamptz NOT NULL DEFAULT (now()),
  "resolved_at" timestamptz,
  "reading_id" bigint
);

ALTER TABLE "bins" ADD FOREIGN KEY ("user_id") REFERENCES "users" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "devices" ADD FOREIGN KEY ("owner_id") REFERENCES "users" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "device_apikeys" ADD FOREIGN KEY ("device_id") REFERENCES "devices" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "setup_codes" ADD FOREIGN KEY ("user_id") REFERENCES "users" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "setup_codes" ADD FOREIGN KEY ("device_id") REFERENCES "devices" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "device_bin_assn" ADD FOREIGN KEY ("device_id") REFERENCES "devices" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "device_bin_assn" ADD FOREIGN KEY ("bin_id") REFERENCES "bins" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "records" ADD FOREIGN KEY ("device_id") REFERENCES "devices" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "records" ADD FOREIGN KEY ("bin_id") REFERENCES "bins" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "device_maintenance" ADD FOREIGN KEY ("device_id") REFERENCES "devices" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "bin_events" ADD FOREIGN KEY ("bin_id") REFERENCES "bins" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "bin_events" ADD FOREIGN KEY ("reading_id") REFERENCES "records" ("id") DEFERRABLE INITIALLY IMMEDIATE;
