"""
Voiceover generation — kie.ai ElevenLabs TTS endpoint.
Tries multiple candidate paths because kie.ai's TTS route varies by account tier.
"""

import base64
import os
from pathlib import Path

import httpx

KIE_BASE = "https://api.kie.ai/api/v1"

# Known ElevenLabs voice IDs (via kie.ai)
VOICE_IDS: dict[str, str] = {
    "Rachel": "21m00Tcm4TlvDq8ikWAM",
    "Antoni": "ErXwobaYiN019PkySvjV",
    "Bella": "EXAVITQu4vr4xnSDxMaL",
    "Josh": "TxGEqnHWrfWFTfGW9XjX",
    "Elli": "MF3mGyEYCl7XYWbV9V6O",
}

# kie.ai candidate TTS endpoints (tried in order until one returns non-404)
TTS_CANDIDATES = [
    "/elevenlabs/text-to-speech",
    "/tts",
    "/elevenlabs/tts",
    "/speech/tts",
    "/voice/tts",
]

TTS_MODEL = "elevenlabs/text-to-speech-multilingual-v2"


def _kie_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('KIE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


async def _find_tts_endpoint(client: httpx.AsyncClient, payload: dict) -> httpx.Response:
    """Try each candidate endpoint and return the first successful response."""
    # Allow override via env var in case user knows exact path
    override = os.getenv("KIE_TTS_ENDPOINT", "").strip()
    candidates = [override] + TTS_CANDIDATES if override else TTS_CANDIDATES

    last_resp = None
    for path in candidates:
        url = f"{KIE_BASE}{path}"
        print(f"[audio_gen] Trying TTS endpoint: {url}")
        resp = await client.post(url, json=payload, headers=_kie_headers())
        if resp.status_code != 404:
            print(f"[audio_gen] Got HTTP {resp.status_code} from {url}")
            return resp
        last_resp = resp

    # All returned 404 — return last response so caller can show a helpful error
    return last_resp


async def generate_voiceover(
    text: str,
    job_dir: Path,
    voice: str = "Rachel",
) -> Path:
    """
    Send text to kie.ai ElevenLabs TTS and save the result as voiceover.mp3.
    Returns the path to the saved file.
    """
    voice_id = VOICE_IDS.get(voice, VOICE_IDS["Rachel"])
    output_path = job_dir / "voiceover.mp3"

    payload = {
        "text": text,
        "model_id": TTS_MODEL,
        "voice_id": voice_id,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await _find_tts_endpoint(client, payload)

    if resp.status_code == 404:
        raise RuntimeError(
            "kie.ai TTS endpoint not found (404 on all candidates). "
            "Set KIE_TTS_ENDPOINT env var to the correct path, "
            "or check kie.ai docs for the TTS route. "
            f"Last URL tried: {resp.url}"
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"ElevenLabs TTS failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )

    content_type = resp.headers.get("content-type", "")

    if "audio" in content_type or "octet-stream" in content_type:
        output_path.write_bytes(resp.content)
    else:
        data = resp.json()
        audio_url = (
            data.get("audioUrl")
            or data.get("audio_url")
            or data.get("url")
            or (data.get("data") or {}).get("audioUrl")
            or (data.get("data") or {}).get("url")
        )
        audio_b64 = (
            data.get("audioBase64")
            or data.get("audio_base64")
            or data.get("audio")
        )

        if audio_url:
            async with httpx.AsyncClient(timeout=60) as dl_client:
                dl = await dl_client.get(audio_url, follow_redirects=True)
                dl.raise_for_status()
                output_path.write_bytes(dl.content)
        elif audio_b64:
            output_path.write_bytes(base64.b64decode(audio_b64))
        else:
            raise RuntimeError(
                f"Unrecognised TTS response format: {str(data)[:400]}"
            )

    print(f"[audio_gen] Voiceover saved → {output_path}")
    return output_path
