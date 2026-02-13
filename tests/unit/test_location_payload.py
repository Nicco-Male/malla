"""Unit tests for Meshtastic raw location payload decoding."""

from malla.utils import location_payload


class _FakeMapReportMissingCoords:
    """MapReport variant that parses successfully but has no coordinates."""

    def __init__(self):
        self.latitude_i = 0
        self.longitude_i = 0
        self.altitude = 0
        self.position_precision = None

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakePositionWithCoords:
    """Legacy Position payload carrying coordinates for MAP_REPORT_APP."""

    def __init__(self):
        self.latitude_i = 409075712
        self.longitude_i = 147718144
        self.altitude = 442
        self.precision_bits = 17

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeMeshPb2Fallback:
    MapReport = _FakeMapReportMissingCoords
    Position = _FakePositionWithCoords


class _FakeMapReportWithCoords:
    def __init__(self):
        self.latitude_i = 409075712
        self.longitude_i = 147718144
        self.altitude = 442
        self.position_precision = 16

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakePositionUnused:
    def __init__(self):
        self.latitude_i = 0
        self.longitude_i = 0
        self.altitude = 0
        self.precision_bits = None

    def ParseFromString(self, _: bytes) -> None:  # noqa: N802
        return


class _FakeMeshPb2Native:
    MapReport = _FakeMapReportWithCoords
    Position = _FakePositionUnused


def test_decode_map_report_falls_back_to_legacy_position_when_mapreport_has_no_coords(
    monkeypatch,
):
    monkeypatch.setattr(location_payload, "mesh_pb2", _FakeMeshPb2Fallback)

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
