"""Unit tests for routes/tts.py — text-to-speech endpoint logic.

`tts.py` defers both the `server` import (inside the auth wrapper) and the
`emergentintegrations` TTS client import (inside the handler), so the module
loads standalone. We drive the handler directly with a fake Request and a
stubbed TTS client to cover: input validation, the in-memory cache (hit, store,
and FIFO eviction), the long-text truncation, and the missing-key guard —
none of which need a live server, OpenAI key, or network.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import routes.tts as tts  # noqa: E402


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_cache():
    tts._tts_cache.clear()
    yield
    tts._tts_cache.clear()


@pytest.fixture
def stub_tts_client(monkeypatch):
    """Inject a fake emergentintegrations.llm.openai.OpenAITextToSpeech that
    returns a deterministic base64 string without any network call."""
    calls = {"count": 0}

    class _FakeTTS:
        def __init__(self, api_key):
            self.api_key = api_key

        async def generate_speech_base64(self, **kwargs):
            calls["count"] += 1
            return "FAKE_AUDIO_B64"

    root = types.ModuleType("emergentintegrations")
    llm = types.ModuleType("emergentintegrations.llm")
    openai_mod = types.ModuleType("emergentintegrations.llm.openai")
    openai_mod.OpenAITextToSpeech = _FakeTTS
    monkeypatch.setitem(sys.modules, "emergentintegrations", root)
    monkeypatch.setitem(sys.modules, "emergentintegrations.llm", llm)
    monkeypatch.setitem(sys.modules, "emergentintegrations.llm.openai", openai_mod)
    monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")
    return calls


async def _call(payload):
    return await tts.text_to_speech(_FakeRequest(payload), current_user=object())


def test_empty_text_raises_400():
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.run(_call({"text": "   "}))
    assert exc.value.status_code == 400


def test_cache_hit_short_circuits_before_key_check():
    # Seed the cache; no EMERGENT_LLM_KEY set → if it fell through it would 500.
    tts._tts_cache["Turn left"] = "CACHED_AUDIO"
    import asyncio
    result = asyncio.run(_call({"text": "Turn left"}))
    assert result == {"audio_base64": "CACHED_AUDIO", "cached": True}


def test_missing_key_raises_500(monkeypatch):
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_call({"text": "Uncached instruction"}))
    assert exc.value.status_code == 500


def test_generation_stores_in_cache(stub_tts_client):
    import asyncio
    result = asyncio.run(_call({"text": "Continue straight"}))
    assert result == {"audio_base64": "FAKE_AUDIO_B64", "cached": False}
    assert tts._tts_cache["Continue straight"] == "FAKE_AUDIO_B64"
    assert stub_tts_client["count"] == 1


def test_second_call_uses_cache_not_client(stub_tts_client):
    import asyncio
    asyncio.run(_call({"text": "Merge right"}))
    asyncio.run(_call({"text": "Merge right"}))
    # Client invoked exactly once; the second call is served from cache.
    assert stub_tts_client["count"] == 1


def test_long_text_truncated_to_500_chars(stub_tts_client):
    import asyncio
    long_text = "x" * 800
    asyncio.run(_call({"text": long_text}))
    # The stored cache key is the truncated text, proving truncation happened
    # before the cache write.
    assert long_text[:500] in tts._tts_cache
    assert long_text not in tts._tts_cache


def test_cache_evicts_fifo_above_200_entries(stub_tts_client):
    import asyncio
    # Pre-fill to the eviction threshold with placeholder entries.
    for i in range(201):
        tts._tts_cache[f"seed-{i}"] = "x"
    oldest = next(iter(tts._tts_cache))
    asyncio.run(_call({"text": "fresh instruction"}))
    # The oldest seeded entry is evicted; the fresh one is present.
    assert oldest not in tts._tts_cache
    assert "fresh instruction" in tts._tts_cache
