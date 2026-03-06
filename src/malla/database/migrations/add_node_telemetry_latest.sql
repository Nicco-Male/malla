-- Optional optimization table for latest node telemetry values
-- Speeds up telemetry queries for statistics dashboards by avoiding
-- repeated protobuf decoding over the full packet_history.

CREATE TABLE IF NOT EXISTS node_telemetry_latest (
    node_id BIGINT PRIMARY KEY,
    temperature FLOAT,
    humidity FLOAT,
    pressure FLOAT,
    battery_level INTEGER,
    voltage FLOAT,
    channel_utilization FLOAT,
    air_util_tx FLOAT,
    last_updated TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE node_telemetry_latest
ADD COLUMN IF NOT EXISTS channel_utilization FLOAT;

ALTER TABLE node_telemetry_latest
ADD COLUMN IF NOT EXISTS air_util_tx FLOAT;

CREATE INDEX IF NOT EXISTS idx_node_telemetry_latest_updated
ON node_telemetry_latest(last_updated DESC);

COMMENT ON TABLE node_telemetry_latest IS 'Latest decoded telemetry values per node for fast stats queries';
