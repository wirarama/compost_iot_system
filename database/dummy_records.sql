delete from users *;
delete from bins *;
delete from devices *;

insert into users (email, password_hash)
values ('test@skibidi.com', '5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8');

insert into devices (mac, owner_id, created_at, is_active)
values ('00:1A:2B:3C:4D:5E', (select id from users limit 1), now(), true);

insert into bins (location, user_id, name, created_at)
values ('Mataram, Lombok, Indonesia', (select id from users limit 1), 'UNRAM_bin_1', now());

select * from users;CREATE TABLE "records" (
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