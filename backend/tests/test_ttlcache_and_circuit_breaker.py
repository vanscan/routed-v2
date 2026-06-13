"""Tests for TTLCache and OSRM circuit breaker in utils/matrices.py.

Covers:
- TTLCache: miss/hit tracking, TTL expiry, LRU eviction, set/overwrite, stats
- Circuit breaker: _osrm_enabled, _osrm_log_failure, _osrm_note_success
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Make the backend package root importable regardless of where pytest is run from
sys.path.insert(0, str(Path(__file__).parent.parent))

# Inject a lightweight stub for `server` so that the deferred
# `from server import OSRM_URL` inside _osrm_enabled() never triggers the
# real server.py import chain (which needs fastapi, dotenv, motor, …).
# Tests that need to control OSRM_URL will monkeypatch this stub directly.
if "server" not in sys.modules:
    _server_stub = types.ModuleType("server")
    _server_stub.OSRM_URL = ""
    _server_stub.OSRM_PUBLIC_URL = ""
    _server_stub.OSRM_URL_PROD = ""
    _server_stub.MAPBOX_TOKEN = ""
    sys.modules["server"] = _server_stub

from utils import matrices  # noqa: E402
from utils.matrices import TTLCache  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def reset_circuit_breaker():
    """Reset OSRM circuit-breaker globals before (and after) each test."""
    matrices._osrm_consecutive_failures = 0
    matrices._osrm_suppress_until = 0.0
    yield
    matrices._osrm_consecutive_failures = 0
    matrices._osrm_suppress_until = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# TTLCache — basic get / set
# ──────────────────────────────────────────────────────────────────────────────

class TestTTLCacheGetSet:
    def test_get_miss_returns_none(self):
        cache = TTLCache()
        assert cache.get("missing") is None

    def test_get_miss_increments_misses(self):
        cache = TTLCache()
        cache.get("absent")
        cache.get("also-absent")
        assert cache.misses == 2

    def test_set_then_get_returns_value(self):
        cache = TTLCache()
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_get_hit_increments_hits(self):
        cache = TTLCache()
        cache.set("k", 42)
        cache.get("k")
        cache.get("k")
        assert cache.hits == 2

    def test_get_hit_does_not_increment_misses(self):
        cache = TTLCache()
        cache.set("k", 1)
        cache.get("k")
        assert cache.misses == 0

    def test_overwrite_updates_value(self):
        cache = TTLCache()
        cache.set("k", "first")
        cache.set("k", "second")
        assert cache.get("k") == "second"


# ──────────────────────────────────────────────────────────────────────────────
# TTLCache — TTL expiry
# ──────────────────────────────────────────────────────────────────────────────

class TestTTLCacheExpiry:
    def test_get_returns_none_after_ttl_expires(self, monkeypatch):
        """Advance time past the TTL and confirm a miss is returned."""
        now = [100.0]
        monkeypatch.setattr(matrices._time, "monotonic", lambda: now[0])

        cache = TTLCache(ttl=10)
        cache.set("k", "v")

        # Within TTL — should hit
        now[0] = 109.9
        assert cache.get("k") == "v"

        # Past TTL — should miss
        now[0] = 110.1
        result = cache.get("k")
        assert result is None

    def test_expired_entry_increments_misses(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(matrices._time, "monotonic", lambda: now[0])

        cache = TTLCache(ttl=5)
        cache.set("x", "y")

        now[0] = 6.0
        cache.get("x")
        assert cache.misses == 1

    def test_overwrite_resets_ttl(self, monkeypatch):
        """After overwriting, the entry should live for a full TTL from the new write."""
        now = [0.0]
        monkeypatch.setattr(matrices._time, "monotonic", lambda: now[0])

        cache = TTLCache(ttl=10)
        cache.set("k", "original")

        # Advance close to expiry then overwrite
        now[0] = 9.0
        cache.set("k", "refreshed")

        # At original expiry time — new entry should still be alive
        now[0] = 10.5
        assert cache.get("k") == "refreshed"

        # Past new TTL
        now[0] = 19.5
        assert cache.get("k") is None


# ──────────────────────────────────────────────────────────────────────────────
# TTLCache — LRU eviction
# ──────────────────────────────────────────────────────────────────────────────

class TestTTLCacheLRUEviction:
    def test_inserting_third_key_evicts_oldest(self):
        """maxsize=2: inserting a 3rd entry evicts the LRU key."""
        cache = TTLCache(maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # "a" should be evicted

        assert cache.get("c") == 3
        assert cache.get("b") == 2
        assert cache.get("a") is None  # evicted

    def test_accessing_entry_prevents_eviction(self):
        """Accessing 'a' before inserting 'c' should promote it; 'b' gets evicted."""
        cache = TTLCache(maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")      # promotes "a" to most-recently-used
        cache.set("c", 3)   # "b" should now be evicted

        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.get("b") is None  # evicted

    def test_maxsize_respected_after_multiple_inserts(self):
        cache = TTLCache(maxsize=3)
        for i in range(10):
            cache.set(f"key{i}", i)
        assert len(cache._cache) <= 3


# ──────────────────────────────────────────────────────────────────────────────
# TTLCache — stats()
# ──────────────────────────────────────────────────────────────────────────────

class TestTTLCacheStats:
    def test_stats_hit_rate_zero_when_no_accesses(self):
        cache = TTLCache()
        s = cache.stats()
        assert s["hit_rate"] == 0

    def test_stats_entries_count(self):
        cache = TTLCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.stats()["entries"] == 2

    def test_stats_hits_and_misses(self):
        cache = TTLCache()
        cache.set("present", True)
        cache.get("present")   # hit
        cache.get("absent")    # miss
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1

    def test_stats_hit_rate_100_percent(self):
        cache = TTLCache()
        cache.set("k", "v")
        cache.get("k")
        assert cache.stats()["hit_rate"] == 100.0

    def test_stats_hit_rate_50_percent(self):
        cache = TTLCache()
        cache.set("k", "v")
        cache.get("k")      # hit
        cache.get("nope")   # miss
        assert cache.stats()["hit_rate"] == 50.0

    def test_stats_returns_maxsize_and_ttl(self):
        cache = TTLCache(maxsize=77, ttl=123)
        s = cache.stats()
        assert s["maxsize"] == 77
        assert s["ttl_seconds"] == 123


# ──────────────────────────────────────────────────────────────────────────────
# OSRM circuit breaker — _osrm_enabled()
# ──────────────────────────────────────────────────────────────────────────────

class TestOsrmEnabled:
    def test_returns_false_when_osrm_url_empty(self, monkeypatch, reset_circuit_breaker):
        monkeypatch.setattr("server.OSRM_URL", "")
        assert matrices._osrm_enabled() is False

    def test_returns_true_when_url_set_and_no_failures(self, monkeypatch, reset_circuit_breaker):
        monkeypatch.setattr("server.OSRM_URL", "http://osrm.local")
        assert matrices._osrm_enabled() is True

    def test_returns_false_after_threshold_failures_within_suppress_window(
        self, monkeypatch, reset_circuit_breaker
    ):
        monkeypatch.setattr("server.OSRM_URL", "http://osrm.local")
        exc = Exception("timeout")
        for _ in range(matrices._OSRM_FAIL_THRESHOLD):
            matrices._osrm_log_failure("ctx", exc)
        # Now suppress window is active; _osrm_enabled should refuse
        assert matrices._osrm_enabled() is False

    def test_returns_true_after_suppress_window_expires(self, monkeypatch, reset_circuit_breaker):
        monkeypatch.setattr("server.OSRM_URL", "http://osrm.local")

        # Set failures above threshold, but put suppress_until in the past
        matrices._osrm_consecutive_failures = matrices._OSRM_FAIL_THRESHOLD
        matrices._osrm_suppress_until = 1.0  # epoch + 1 second — always in the past

        assert matrices._osrm_enabled() is True

    def test_returns_true_after_note_success_resets_counter(
        self, monkeypatch, reset_circuit_breaker
    ):
        monkeypatch.setattr("server.OSRM_URL", "http://osrm.local")
        exc = Exception("gone")
        for _ in range(matrices._OSRM_FAIL_THRESHOLD):
            matrices._osrm_log_failure("ctx", exc)

        # Simulate a successful response
        matrices._osrm_note_success()
        assert matrices._osrm_enabled() is True


# ──────────────────────────────────────────────────────────────────────────────
# OSRM circuit breaker — _osrm_log_failure()
# ──────────────────────────────────────────────────────────────────────────────

class TestOsrmLogFailure:
    def test_below_threshold_does_not_suppress(self, monkeypatch, reset_circuit_breaker):
        monkeypatch.setattr("server.OSRM_URL", "http://osrm.local")
        exc = Exception("err")
        for _ in range(matrices._OSRM_FAIL_THRESHOLD - 1):
            matrices._osrm_log_failure("ctx", exc)
        # Not at threshold yet — should still be enabled
        assert matrices._osrm_enabled() is True

    def test_increments_failure_counter(self, reset_circuit_breaker):
        matrices._osrm_log_failure("ctx", Exception("e"))
        assert matrices._osrm_consecutive_failures == 1

    def test_sets_suppress_until_at_threshold(self, monkeypatch, reset_circuit_breaker):
        fake_now = [1_000_000.0]
        monkeypatch.setattr(matrices._time, "time", lambda: fake_now[0])

        exc = Exception("boom")
        for _ in range(matrices._OSRM_FAIL_THRESHOLD):
            matrices._osrm_log_failure("ctx", exc)

        expected_suppress = fake_now[0] + matrices._OSRM_SUPPRESS_SECONDS
        assert matrices._osrm_suppress_until == pytest.approx(expected_suppress)

    def test_further_failures_do_not_reset_suppress_until(self, monkeypatch, reset_circuit_breaker):
        """Extra failures beyond the threshold must not push the window further out."""
        fake_now = [1_000_000.0]
        monkeypatch.setattr(matrices._time, "time", lambda: fake_now[0])

        exc = Exception("repeated")
        for _ in range(matrices._OSRM_FAIL_THRESHOLD):
            matrices._osrm_log_failure("ctx", exc)

        first_suppress = matrices._osrm_suppress_until

        # Advance time a little and log one more failure
        fake_now[0] += 10.0
        matrices._osrm_log_failure("ctx", exc)

        # suppress_until must not have moved (we are still within the window)
        assert matrices._osrm_suppress_until == pytest.approx(first_suppress)


# ──────────────────────────────────────────────────────────────────────────────
# OSRM circuit breaker — _osrm_note_success()
# ──────────────────────────────────────────────────────────────────────────────

class TestOsrmNoteSuccess:
    def test_resets_failure_counter_to_zero(self, reset_circuit_breaker):
        matrices._osrm_consecutive_failures = 5
        matrices._osrm_note_success()
        assert matrices._osrm_consecutive_failures == 0

    def test_resets_suppress_until_to_zero(self, reset_circuit_breaker):
        matrices._osrm_suppress_until = 9_999_999.0
        matrices._osrm_consecutive_failures = 1  # must be non-zero to trigger the reset
        matrices._osrm_note_success()
        assert matrices._osrm_suppress_until == 0.0

    def test_idempotent_when_already_clean(self, reset_circuit_breaker):
        """Calling note_success on a clean breaker should not raise."""
        matrices._osrm_note_success()
        assert matrices._osrm_consecutive_failures == 0
        assert matrices._osrm_suppress_until == 0.0
