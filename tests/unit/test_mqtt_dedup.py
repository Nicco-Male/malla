"""Bounded MQTT replay cache behavior."""

from malla.utils.mqtt_dedup import RecentMQTTMessages


def test_exact_topic_payload_pair():
    cache = RecentMQTTMessages()
    key = cache.fingerprint("msh/US/2/e/LongFast/a", b"payload")
    assert not cache.contains(key)  # First DB attempt not yet committed.
    cache.remember(key)
    assert cache.contains(key)
    assert not cache.contains(cache.fingerprint("msh/US/2/e/LongFast/b", b"payload"))
    assert not cache.contains(cache.fingerprint("msh/US/2/e/LongFast/a", b"other"))


def test_time_window():
    now = [0.0]
    cache = RecentMQTTMessages(window_seconds=600, clock=lambda: now[0])
    key = cache.fingerprint("topic", b"payload")
    cache.remember(key)
    now[0] = 599
    assert cache.contains(key)
    now[0] = 600
    assert not cache.contains(key)


def test_memory_cap_and_disabled_mode():
    cache = RecentMQTTMessages(max_entries=2)
    keys = [cache.fingerprint("topic", bytes([n])) for n in range(3)]
    for key in keys:
        cache.remember(key)
    assert not cache.contains(keys[0])
    assert cache.contains(keys[1])
    assert cache.contains(keys[2])
    disabled = RecentMQTTMessages(window_seconds=0)
    disabled.remember(keys[0])
    assert not disabled.contains(keys[0])
