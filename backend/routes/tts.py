"""Text-to-Speech endpoint — navigation instruction audio via OpenAI TTS.

    POST /tts  → returns base64-encoded mp3 for a short instruction string

Moved verbatim from server.py. Fully self-contained: the in-memory
`_tts_cache` lives here, `get_current_user` is lazy-imported from `server`
inside a thin dependency wrapper (same deferred-import pattern as the other
route modules), and the OpenAI TTS client is imported lazily inside the
handler so the module loads cleanly even when the integration isn't present.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger("server")
router = APIRouter()

_tts_cache: dict[str, str] = {}  # text -> base64 audio cache


async def _current_user(request: Request):
    """Dep wrapper — defers the `server` import until the first request."""
    from server import get_current_user  # noqa: WPS433
    return await get_current_user(request)


@router.post("/tts")
async def text_to_speech(request: Request, current_user=Depends(_current_user)):
    """Generate speech audio from navigation instruction text using OpenAI TTS"""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")
    if len(text) > 500:
        text = text[:500]

    # Check in-memory cache
    if text in _tts_cache:
        return {"audio_base64": _tts_cache[text], "cached": True}

    llm_key = os.environ.get("EMERGENT_LLM_KEY")
    if not llm_key:
        raise HTTPException(status_code=500, detail="TTS key not configured")

    try:
        from emergentintegrations.llm.openai import OpenAITextToSpeech
        tts = OpenAITextToSpeech(api_key=llm_key)
        audio_b64 = await tts.generate_speech_base64(
            text=text,
            model="tts-1",
            voice="nova",
            speed=1.1,
            response_format="mp3",
        )
        # Cache (limit to 200 entries)
        if len(_tts_cache) > 200:
            _tts_cache.pop(next(iter(_tts_cache)))
        _tts_cache[text] = audio_b64
        return {"audio_base64": audio_b64, "cached": False}
    except Exception as e:
        logger.error("TTS generation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")
