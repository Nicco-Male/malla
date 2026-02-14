"""Unit tests for LocationRepository."""

from unittest.mock import Mock, patch

import pytest
from src.malla.cache import clear_cache
from src.malla.database.repositories import LocationRepository


class TestLocationRepository:
    """Test LocationRepository methods."""

    @pytest.mark.unit
    @patch("src.malla.database.repositories.mesh_pb2.MapReport", create=True)
    @patch("src.malla.database.repositories.mesh_pb2.Position")
    @patch("src.malla.database.repositories.put_db_connection")
    @patch("src.malla.database.repositories.get_db_connection")
    def test_get_node_locations_decodes_position_and_map_report(
        self, mock_get_db, mock_put_db, mock_position_cls, mock_map_report_cls
    ):
        """It decodes both POSITION_APP (3) and MAP_REPORT_APP (73) rows."""
        clear_cache()

        position_payload = b"position-payload"
        map_report_payload = b"map-report-payload"

        mock_position = Mock(
            latitude_i=450123456,
            longitude_i=91234567,
            altitude=250,
            precision_bits=19,
            sats_in_view=9,
        )
        mock_position.ParseFromString.return_value = None
        mock_position_cls.return_value = mock_position

        mock_map_report = Mock(
            latitude_i=451234567,
            longitude_i=91345678,
            altitude=300,
            position_precision=16,
        )
        mock_map_report.ParseFromString.return_value = None
        mock_map_report_cls.return_value = mock_map_report

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            {
                "node_id": 1128074276,
                "portnum": 3,
                "timestamp": 1710000000,
                "raw_payload": position_payload,
                "row_num": 1,
                "long_name": "Node Position",
                "short_name": "NP",
                "hw_model": "TBEAM",
                "role": "ROUTER",
                "primary_channel": 1,
                "hex_id": "!433d0c24",
            },
            {
                "node_id": 1128074277,
                "portnum": 73,
                "timestamp": 1710000001,
                "raw_payload": map_report_payload,
                "row_num": 1,
                "long_name": "Node Map",
                "short_name": "NM",
                "hw_model": "TDECK",
                "role": "CLIENT",
                "primary_channel": 2,
                "hex_id": "!433d0c25",
            },
        ]

        locations = LocationRepository.get_node_locations()

        assert len(locations) == 2

        by_node = {item["node_id"]: item for item in locations}

        assert by_node[1128074276]["latitude"] == pytest.approx(45.0123456)
        assert by_node[1128074276]["longitude"] == pytest.approx(9.1234567)
        assert by_node[1128074276]["sats_in_view"] == 9

        assert by_node[1128074277]["latitude"] == pytest.approx(45.1234567)
        assert by_node[1128074277]["longitude"] == pytest.approx(9.1345678)
        assert by_node[1128074277]["position_precision"] == 16

        mock_get_db.assert_called_once()
        mock_put_db.assert_called_once_with(mock_conn)
