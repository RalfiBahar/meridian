-- Enable TimescaleDB on first boot of the Postgres data directory.
-- This file is mounted into /docker-entrypoint-initdb.d/ and runs exactly once,
-- when the data directory is empty. Subsequent boots skip it.
CREATE EXTENSION IF NOT EXISTS timescaledb;
