"""Helpers for decoding location data from raw Meshtastic payloads."""

from typing import Any

from meshtastic import mesh_pb2


def _decode_position_message(raw_payload: bytes) -> tuple[int | None, int | None, int | None, int | None]:
    """Decode payload as a Position protobuf."""
    position = mesh_pb2.Position()
    position.ParseFromString(raw_payload)
    latitude_i = position.latitude_i
    longitude_i = position.longitude_i
    altitude = position.altitude if position.altitude else None
    precision = getattr(position, "precision_bits", None)
    return latitude_i, longitude_i, altitude, precision


def decode_position_from_raw_payload(
    portnum: int | None, raw_payload: bytes | None
) -> dict[str, Any] | None:
    """Decode latitude/longitude/altitude/precision from POSITION/MAP_REPORT payloads."""
    if not raw_payload or portnum not in {3, 73}:
        return None

    if portnum == 3:  # POSITION_APP
        latitude_i, longitude_i, altitude, precision = _decode_position_message(raw_payload)
    else:  # MAP_REPORT_APP
        latitude_i = longitude_i = altitude = precision = None

        # Some meshtastic Python releases don't expose MapReport at all.
        map_report_cls = getattr(mesh_pb2, "MapReport", None)
        if map_report_cls is not None:
            map_report = map_report_cls()
            map_report.ParseFromString(raw_payload)
            latitude_i = getattr(map_report, "latitude_i", None)
            longitude_i = getattr(map_report, "longitude_i", None)
            altitude = map_report.altitude if getattr(map_report, "altitude", 0) else None
            precision = getattr(map_report, "position_precision", None)

        # Legacy/community MAP_REPORT payloads are Position-encoded.
        if not latitude_i or not longitude_i:
            latitude_i, longitude_i, altitude, precision = _decode_position_message(raw_payload)

    return {
        "latitude": (latitude_i / 1e7) if latitude_i else None,
        "longitude": (longitude_i / 1e7) if longitude_i else None,
        "altitude": altitude,
        "precision": precision,
    }
