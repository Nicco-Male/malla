"""Unit tests for Meshtastic raw location payload decoding."""

import logging

from malla.utils import location_payload


class _FakePositionWithCoords:
    """Legacy Position payload carrying coordinates for MAP_REPORT_APP."""

    def __init__(self):
        self.latitude_i = 409075712
        self.longitude_i = 147718144
        self.altitude = 442
        self.precision_bits = 17

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakePositionWithoutCoords:
    """Position payload with no coordinates."""

    def __init__(self):
        self.latitude_i = 0
        self.longitude_i = 0
        self.altitude = 0
        self.precision_bits = None

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeNestedPositionWithCoords:
    def __init__(self):
        self.latitude_i = 409075712
        self.longitude_i = 147718144
        self.altitude = 500
        self.precision_bits = 14


class _FakeMapReportMissingCoords:
    """MapReport variant that parses but has no coordinates."""

    def __init__(self):
        self.latitude_i = 0
        self.longitude_i = 0
        self.altitude = 0
        self.position_precision = None

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeMapReportNestedCoords:
    """MapReport variant with nested position coordinates."""

    def __init__(self):
        self.latitude_i = 0
        self.longitude_i = 0
        self.altitude = 0
        self.position_precision = None
        self.position = _FakeNestedPositionWithCoords()

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeMapReportWithCoords:
    def __init__(self):
        self.latitude_i = 409075712
        self.longitude_i = 147718144
        self.altitude = 442
        self.position_precision = 16

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeMeshPb2WithoutMapReport:
    Position = _FakePositionWithCoords


class _FakeMeshPb2Fallback:
    MapReport = _FakeMapReportMissingCoords
    Position = _FakePositionWithCoords


class _FakeMeshPb2NoCoordsAnywhere:
    MapReport = _FakeMapReportMissingCoords
    Position = _FakePositionWithoutCoords


class _FakeMeshPb2Native:
    MapReport = _FakeMapReportWithCoords
    Position = _FakePositionWithCoords


class _FakeMeshPb2Nested:
    MapReport = _FakeMapReportNestedCoords
    Position = _FakePositionWithCoords


def test_decode_map_report_works_when_mapreport_class_is_missing(monkeypatch):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2WithoutMapReport)

    decoded = location_payload.decode_position_from_raw_payload(73, b"dummy")

    assert decoded == {
        "latitude": 40.9075712,
        "longitude": 14.7718144,
        "altitude": 442,
        "precision": 17,
    }


def test_decode_map_report_with_nested_coordinates(monkeypatch):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2Nested)

    decoded = location_payload.decode_position_from_raw_payload(73, b"dummy")

    assert decoded == {
        "latitude": 40.9075712,
        "longitude": 14.7718144,
        "altitude": 500,
        "precision": 14,
    }


def test_decode_map_report_returns_none_when_no_coordinates_exist(monkeypatch, caplog):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2NoCoordsAnywhere)
    monkeypatch.setattr(location_payload, "MessageToDict", lambda *args, **kwargs: {})

    with caplog.at_level(logging.DEBUG):
        decoded = location_payload.decode_position_from_raw_payload(73, b"dummy")

    assert decoded is None
    assert "MAP_REPORT decoded but no lat/lon fields found" in caplog.text


def test_decode_map_report_falls_back_to_legacy_position_when_mapreport_has_no_coords(
    monkeypatch,
):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2Fallback)
    monkeypatch.setattr(location_payload, "MessageToDict", lambda *args, **kwargs: {})

    decoded = location_payload.decode_position_from_raw_payload(73, b"dummy")

    assert decoded == {
        "latitude": 40.9075712,
        "longitude": 14.7718144,
        "altitude": 442,
        "precision": 17,
    }


def test_decode_map_report_prefers_mapreport_when_coordinates_exist(monkeypatch):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2Native)

    decoded = location_payload.decode_position_from_raw_payload(73, b"dummy")

    assert decoded == {
        "latitude": 40.9075712,
        "longitude": 14.7718144,
        "altitude": 442,
        "precision": 16,
    }
