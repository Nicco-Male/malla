from malla.mqtt_capture import normalize_voltage
from malla.services.analytics_service import AnalyticsService
from malla.database.repositories import NodeRepository


def test_normalize_voltage_keeps_volts_values() -> None:
    assert normalize_voltage(4.09) == 4.09
    assert AnalyticsService._normalize_voltage(3.7) == 3.7


def test_normalize_voltage_converts_millivolts_values() -> None:
    assert normalize_voltage(4090.0) == 4.09
    assert AnalyticsService._normalize_voltage(3700.0) == 3.7


def test_repository_normalize_voltage_backfills_legacy_scaled_values() -> None:
    assert NodeRepository._normalize_voltage_value(0.00409) == 4.09


def test_repository_normalize_voltage_handles_none() -> None:
    assert NodeRepository._normalize_voltage_value(None) is None
