"""Helpers for decoding location data from raw Meshtastic payloads."""

import logging
from typing import Any

from google.protobuf.json_format import MessageToDict
from meshtastic import mesh_pb2

logger = logging.getLogger(__name__)


def _decode_position_message(raw_payload: bytes) -> tuple[int | None, int | None, int | None, int | None]:
    """Decode payload as a Position protobuf."""
    position = mesh_pb2.Position()
    position.ParseFromString(raw_payload)
    latitude_i = position.latitude_i
    longitude_i = position.longitude_i
    altitude = position.altitude if position.altitude else None
    precision = getattr(position, "precision_bits", None)
    return latitude_i, longitude_i, altitude, precision


def _extract_map_report_coords(map_report: Any) -> tuple[int | None, int | None, int | None, int | None]:
    """Extract coords from MapReport across known schema variants."""
    latitude_i = getattr(map_report, "latitude_i", None)
    longitude_i = getattr(map_report, "longitude_i", None)
    altitude = map_report.altitude if getattr(map_report, "altitude", 0) else None
    precision = getattr(map_report, "position_precision", None)

    if latitude_i and longitude_i:
        return latitude_i, longitude_i, altitude, precision

    nested_position = getattr(map_report, "position", None)
    if nested_position is not None:
        latitude_i = getattr(nested_position, "latitude_i", None)
        longitude_i = getattr(nested_position, "longitude_i", None)
        altitude = getattr(nested_position, "altitude", None) or altitude
        precision = getattr(nested_position, "precision_bits", None) or precision
        if latitude_i and longitude_i:
            return latitude_i, longitude_i, altitude, precision

    return None, None, altitude, precision


def _find_lat_lon_in_dict(data: Any) -> tuple[int | None, int | None]:
    """Recursively search a dict/list for known latitude/longitude key pairs."""
    lat_keys = {"latitudeI", "latitude_i", "latI", "lat_i"}
    lon_keys = {"longitudeI", "longitude_i", "lonI", "lon_i", "lngI", "lng_i"}

    if isinstance(data, dict):
        for lat_key in lat_keys:
            for lon_key in lon_keys:
                lat = data.get(lat_key)
                lon = data.get(lon_key)
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and lat and lon:
                    return int(lat), int(lon)

        for value in data.values():
            lat, lon = _find_lat_lon_in_dict(value)
            if lat and lon:
                return lat, lon

    if isinstance(data, list):
        for item in data:
            lat, lon = _find_lat_lon_in_dict(item)
            if lat and lon:
                return lat, lon

    return None, None


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
            latitude_i, longitude_i, altitude, precision = _extract_map_report_coords(map_report)

            if not latitude_i or not longitude_i:
                map_report_dict = MessageToDict(
                    map_report,
                    preserving_proto_field_name=True,
                    including_default_value_fields=True,
                )
                dict_lat, dict_lon = _find_lat_lon_in_dict(map_report_dict)
                if dict_lat and dict_lon:
                    latitude_i, longitude_i = dict_lat, dict_lon
                else:
                    logger.debug("MAP_REPORT decoded but no lat/lon fields found")

        # Legacy/community MAP_REPORT payloads are Position-encoded.
        if not latitude_i or not longitude_i:
            logger.debug("MAP_REPORT missing usable coordinates, attempting Position fallback")
            latitude_i, longitude_i, altitude, precision = _decode_position_message(raw_payload)

    if not latitude_i or not longitude_i:
        return None

    return {
        "latitude": (latitude_i / 1e7) if latitude_i else None,
        "longitude": (longitude_i / 1e7) if longitude_i else None,
        "altitude": altitude,
        "precision": precision,
    }
