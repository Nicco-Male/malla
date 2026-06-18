from malla.database.repositories import NodeRepository
from malla.mqtt_capture import normalize_voltage
from malla.services.analytics_service import AnalyticsService


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


class _FakePowerMetrics:
    def __init__(self, **values: float) -> None:
        self._fields = set(values)
        for key, value in values.items():
            setattr(self, key, value)

    def HasField(self, field_name: str) -> bool:
        return field_name in self._fields


def test_power_metrics_voltage_uses_first_populated_positive_channel() -> None:
    power_metrics = _FakePowerMetrics(
        ch1_voltage=3.728,
        ch1_current=15.2,
        ch2_voltage=5.52,
    )

    assert AnalyticsService._extract_power_metrics_voltage(power_metrics) == 3.728


def test_power_metrics_voltage_skips_zero_channels() -> None:
    power_metrics = _FakePowerMetrics(ch1_voltage=0.0, ch2_voltage=5520.0)

    assert AnalyticsService._extract_power_metrics_voltage(power_metrics) == 5.52
