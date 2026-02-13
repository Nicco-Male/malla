"""Helpers for decoding location data from raw Meshtastic payloads."""

from typing import Any

from meshtastic import mesh_pb2


def decode_position_from_raw_payload(
    portnum: int | None, raw_payload: bytes | None
) -> dict[str, Any] | None:
    """Decode latitude/longitude/altitude/precision from POSITION/MAP_REPORT payloads."""
    if not raw_payload or portnum not in {3, 73}:
        return None

    if portnum == 3:  # POSITION_APP
        position = mesh_pb2.Position()
        position.ParseFromString(raw_payload)
        latitude_i = position.latitude_i
        longitude_i = position.longitude_i
        altitude = position.altitude if position.altitude else None
        precision = getattr(position, "precision_bits", None)
    else:  # MAP_REPORT_APP
        # MAP_REPORT_APP payloads are seen in two wire-compatible variants in the wild:
        # - MapReport message (newer encoding)
        # - Position message (legacy/community maps)
        # Parse as MapReport first, then fallback to Position if coordinates are missing.
        map_report = mesh_pb2.MapReport()
        map_report.ParseFromString(raw_payload)
        latitude_i = map_report.latitude_i
        longitude_i = map_report.longitude_i
        altitude = map_report.altitude if map_report.altitude else None
        precision = getattr(map_report, "position_precision", None)

        if not latitude_i or not longitude_i:
            legacy_position = mesh_pb2.Position()
            legacy_position.ParseFromString(raw_payload)
            latitude_i = legacy_position.latitude_i
            longitude_i = legacy_position.longitude_i
            altitude = legacy_position.altitude if legacy_position.altitude else None
            precision = getattr(legacy_position, "precision_bits", None)

    return {
        "latitude": (latitude_i / 1e7) if latitude_i else None,
        "longitude": (longitude_i / 1e7) if longitude_i else None,
        "altitude": altitude,
        "precision": precision,
    }
