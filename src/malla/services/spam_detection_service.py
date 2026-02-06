"""
Spam detection service for Meshtastic Mesh Health Web UI.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from psycopg2.extras import RealDictCursor

from ..config import get_config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SpamThresholds:
    rate_5m_per_minute: float
    rate_1h_per_minute: float
    rate_24h_per_minute: float
    duplicate_window_seconds: int
    duplicate_threshold: int


class SpamDetectionService:
    """Service to compute spam metrics and auto-block nodes."""

    AUTO_BLOCK_REASON = "spam_threshold_exceeded"
    _LAST_DUPLICATE_REFRESH_TS: float | None = None

    @staticmethod
    def get_spam_thresholds() -> SpamThresholds:
        """Load thresholds from configuration."""
        config = get_config()
        return SpamThresholds(
            rate_5m_per_minute=float(config.spam_rate_threshold_5m_per_minute),
            rate_1h_per_minute=float(config.spam_rate_threshold_1h_per_minute),
            rate_24h_per_minute=float(config.spam_rate_threshold_24h_per_minute),
            duplicate_window_seconds=int(config.spam_duplicate_window_seconds),
            duplicate_threshold=int(config.spam_duplicate_threshold),
        )

    @staticmethod
    def ensure_schema(cursor: Any) -> None:
        """Ensure spam-related tables exist."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS blocklist (
                id SERIAL PRIMARY KEY,
                node_id BIGINT NOT NULL,
                reason TEXT,
                auto_blocked BOOLEAN DEFAULT FALSE,
                metrics JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT blocklist_unique_node UNIQUE (node_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_blocklist_node_id
            ON blocklist(node_id)
            """
        )

    @staticmethod
    def build_duplicate_detection_view(cursor: Any) -> None:
        """Create a view to surface recent duplicate packets for spam checks."""
        cursor.execute(
            """
            CREATE OR REPLACE VIEW packet_duplicates_recent AS
            SELECT
                from_node_id,
                COALESCE(mesh_packet_id::TEXT, encode(digest(raw_payload, 'sha256'), 'hex')) AS payload_key,
                COUNT(*) AS duplicate_count,
                MIN(timestamp) AS first_seen_ts,
                MAX(timestamp) AS last_seen_ts
            FROM packet_history
            WHERE from_node_id IS NOT NULL
              AND timestamp >= EXTRACT(EPOCH FROM NOW() - INTERVAL '1 day')
              AND (
                  mesh_packet_id IS NOT NULL
                  OR raw_payload IS NOT NULL
              )
            GROUP BY from_node_id, payload_key
            """
        )

    @staticmethod
    def refresh_duplicate_materialized_view(conn: Any) -> None:
        """Refresh the materialized view if it exists."""
        cursor = None
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                SELECT 1
                FROM pg_matviews
                WHERE schemaname = 'public' AND matviewname = 'packet_duplicates_recent'
                """
            )
            if cursor.fetchone():
                cursor.execute("REFRESH MATERIALIZED VIEW packet_duplicates_recent")
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping materialized view refresh: %s", exc)
            conn.rollback()
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def maybe_refresh_duplicate_materialized_view(conn: Any) -> None:
        """Refresh the materialized view on a configured cadence."""
        config = get_config()
        refresh_interval = max(1, int(config.spam_duplicate_view_refresh_seconds))
        now_ts = time.time()
        last_refresh = SpamDetectionService._LAST_DUPLICATE_REFRESH_TS
        if last_refresh is None or now_ts - last_refresh >= refresh_interval:
            SpamDetectionService.refresh_duplicate_materialized_view(conn)
            SpamDetectionService._LAST_DUPLICATE_REFRESH_TS = now_ts

    @staticmethod
    def get_spam_metrics(
        conn: Any,
        gateway_id: str | None = None,
        from_node: int | None = None,
        since_seconds: int = 24 * 3600,
    ) -> dict[str, Any]:
        """Compute spam metrics (rate windows + duplicates) from packet history."""
        SpamDetectionService.maybe_refresh_duplicate_materialized_view(conn)
        cursor = None
        now_query = "EXTRACT(EPOCH FROM NOW())"
        filters: list[str] = [f"ph.timestamp >= ({now_query} - %s)"]
        params: list[Any] = [since_seconds]

        if gateway_id:
            filters.append("ph.gateway_id = %s")
            params.append(gateway_id)
        if from_node is not None:
            filters.append("ph.from_node_id = %s")
            params.append(from_node)

        where_clause = " AND ".join(filters)

        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT ph.from_node_id, ph.timestamp
                    FROM packet_history ph
                    WHERE ph.from_node_id IS NOT NULL AND {where_clause}
                ),
                rates AS (
                    SELECT
                        from_node_id,
                        COUNT(*) FILTER (WHERE timestamp >= ({now_query} - 300)) AS count_5m,
                        COUNT(*) FILTER (WHERE timestamp >= ({now_query} - 3600)) AS count_1h,
                        COUNT(*) FILTER (WHERE timestamp >= ({now_query} - 86400)) AS count_24h
                    FROM base
                    GROUP BY from_node_id
                )
                SELECT
                    r.from_node_id,
                    r.count_5m,
                    r.count_1h,
                    r.count_24h
                FROM rates r
                """,
                params,
            )
            rate_rows = cursor.fetchall() or []
        finally:
            if cursor:
                cursor.close()

        rate_metrics: dict[int, dict[str, Any]] = {}
        for row in rate_rows:
            node_id = row["from_node_id"]
            count_5m = row["count_5m"] or 0
            count_1h = row["count_1h"] or 0
            count_24h = row["count_24h"] or 0
            rate_metrics[int(node_id)] = {
                "window_counts": {
                    "5m": int(count_5m),
                    "1h": int(count_1h),
                    "24h": int(count_24h),
                },
                "rates_per_minute": {
                    "5m": round(count_5m / 5.0, 2),
                    "1h": round(count_1h / 60.0, 2),
                    "24h": round(count_24h / 1440.0, 2),
                },
            }

        duplicate_metrics = SpamDetectionService._get_duplicate_metrics(
            conn,
            gateway_id=gateway_id,
            from_node=from_node,
        )

        for node_id, dup in duplicate_metrics.items():
            rate_metrics.setdefault(node_id, {})
            rate_metrics[node_id]["duplicates"] = dup

        return rate_metrics

    @staticmethod
    def _get_duplicate_metrics(
        conn: Any,
        gateway_id: str | None = None,
        from_node: int | None = None,
    ) -> dict[int, dict[str, Any]]:
        cursor = None
        config = get_config()
        window_seconds = max(1, int(config.spam_duplicate_window_seconds))
        filters: list[str] = []
        params: list[Any] = []
        if gateway_id:
            filters.append("ph.gateway_id = %s")
            params.append(gateway_id)
        if from_node is not None:
            filters.append("ph.from_node_id = %s")
            params.append(from_node)
        extra_where = f" AND {' AND '.join(filters)}" if filters else ""

        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                SELECT 1
                FROM pg_matviews
                WHERE schemaname = 'public' AND matviewname = 'packet_duplicates_recent'
                """
            )
            use_mat_view = cursor.fetchone() is not None and gateway_id is None

            if use_mat_view:
                query = f"""
                    SELECT
                        from_node_id,
                        COUNT(*) AS duplicate_groups,
                        SUM(duplicate_count) AS duplicate_total,
                        MAX(duplicate_count) AS max_duplicate_count,
                        MAX(last_seen_ts) AS last_duplicate_ts
                    FROM packet_duplicates_recent
                    WHERE (last_seen_ts - first_seen_ts) <= %s
                    {extra_where.replace('ph.', '')}
                    GROUP BY from_node_id
                """
                cursor.execute(query, [window_seconds, *params])
            else:
                query = f"""
                    WITH base AS (
                        SELECT
                            ph.from_node_id,
                            COALESCE(ph.mesh_packet_id::TEXT, encode(digest(ph.raw_payload, 'sha256'), 'hex')) AS payload_key,
                            ph.timestamp
                        FROM packet_history ph
                        WHERE ph.from_node_id IS NOT NULL
                          AND ph.timestamp >= EXTRACT(EPOCH FROM NOW() - INTERVAL '1 day')
                          AND (ph.mesh_packet_id IS NOT NULL OR ph.raw_payload IS NOT NULL)
                          {extra_where}
                    ),
                    duplicates AS (
                        SELECT
                            from_node_id,
                            payload_key,
                            COUNT(*) AS duplicate_count,
                            MIN(timestamp) AS first_seen_ts,
                            MAX(timestamp) AS last_seen_ts
                        FROM base
                        GROUP BY from_node_id, payload_key
                        HAVING COUNT(*) > 1
                           AND (MAX(timestamp) - MIN(timestamp)) <= %s
                    )
                    SELECT
                        from_node_id,
                        COUNT(*) AS duplicate_groups,
                        SUM(duplicate_count) AS duplicate_total,
                        MAX(duplicate_count) AS max_duplicate_count,
                        MAX(last_seen_ts) AS last_duplicate_ts
                    FROM duplicates
                    GROUP BY from_node_id
                """
                cursor.execute(query, [*params, window_seconds])
            rows = cursor.fetchall() or []
        finally:
            if cursor:
                cursor.close()

        metrics: dict[int, dict[str, Any]] = {}
        for row in rows:
            node_id = int(row["from_node_id"])
            metrics[node_id] = {
                "duplicate_groups": int(row["duplicate_groups"] or 0),
                "duplicate_total": int(row["duplicate_total"] or 0),
                "max_duplicate_count": int(row["max_duplicate_count"] or 0),
                "last_duplicate_ts": float(row["last_duplicate_ts"])
                if row.get("last_duplicate_ts") is not None
                else None,
            }
        return metrics

    @staticmethod
    def evaluate_and_block(
        conn: Any,
        gateway_id: str | None = None,
        from_node: int | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate spam metrics and auto-block nodes exceeding thresholds."""
        config = get_config()
        if not config.spam_auto_block_enabled:
            return []

        SpamDetectionService.maybe_refresh_duplicate_materialized_view(conn)
        thresholds = SpamDetectionService.get_spam_thresholds()
        metrics = SpamDetectionService.get_spam_metrics(
            conn, gateway_id=gateway_id, from_node=from_node
        )
        blocked: list[dict[str, Any]] = []
        cursor = None

        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            for node_id, metric in metrics.items():
                rates = metric.get("rates_per_minute", {})
                duplicates = metric.get("duplicates", {})
                rate_5m = rates.get("5m", 0.0)
                rate_1h = rates.get("1h", 0.0)
                rate_24h = rates.get("24h", 0.0)
                max_duplicates = duplicates.get("max_duplicate_count", 0)

                exceeds_rate = (
                    rate_5m >= thresholds.rate_5m_per_minute
                    or rate_1h >= thresholds.rate_1h_per_minute
                    or rate_24h >= thresholds.rate_24h_per_minute
                )
                exceeds_duplicates = max_duplicates >= thresholds.duplicate_threshold

                if not (exceeds_rate or exceeds_duplicates):
                    continue

                metrics_payload = {
                    "rates_per_minute": rates,
                    "window_counts": metric.get("window_counts", {}),
                    "duplicates": duplicates,
                    "thresholds": {
                        "rate_5m_per_minute": thresholds.rate_5m_per_minute,
                        "rate_1h_per_minute": thresholds.rate_1h_per_minute,
                        "rate_24h_per_minute": thresholds.rate_24h_per_minute,
                        "duplicate_threshold": thresholds.duplicate_threshold,
                    },
                    "reasons": {
                        "rate": exceeds_rate,
                        "duplicates": exceeds_duplicates,
                    },
                }

                cursor.execute(
                    """
                    INSERT INTO blocklist (node_id, reason, auto_blocked, metrics)
                    VALUES (%s, %s, TRUE, %s::jsonb)
                    ON CONFLICT (node_id)
                    DO UPDATE SET
                        reason = EXCLUDED.reason,
                        auto_blocked = TRUE,
                        metrics = EXCLUDED.metrics,
                        updated_at = NOW()
                    """,
                    (node_id, SpamDetectionService.AUTO_BLOCK_REASON, json.dumps(metrics_payload)),
                )
                blocked.append(
                    {
                        "node_id": node_id,
                        "reason": SpamDetectionService.AUTO_BLOCK_REASON,
                        "metrics": metrics_payload,
                    }
                )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to auto-block nodes: %s", exc)
            conn.rollback()
        finally:
            if cursor:
                cursor.close()

        return blocked

    @staticmethod
    def get_blocklist_entries(
        conn: Any,
        limit: int = 100,
        include_metrics: bool = True,
    ) -> list[dict[str, Any]]:
        """Return blocklist entries for the wall of block."""
        cursor = None
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                SELECT
                    node_id,
                    reason,
                    auto_blocked,
                    metrics,
                    EXTRACT(EPOCH FROM created_at) AS created_at_ts,
                    EXTRACT(EPOCH FROM updated_at) AS updated_at_ts
                FROM blocklist
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall() or []
        finally:
            if cursor:
                cursor.close()

        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = {
                "node_id": int(row["node_id"]),
                "reason": row.get("reason"),
                "auto_blocked": bool(row.get("auto_blocked")),
                "created_at_ts": float(row.get("created_at_ts"))
                if row.get("created_at_ts") is not None
                else None,
                "updated_at_ts": float(row.get("updated_at_ts"))
                if row.get("updated_at_ts") is not None
                else None,
            }
            if include_metrics:
                entry["metrics"] = row.get("metrics") or {}
            entries.append(entry)
        return entries
