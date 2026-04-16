"""
Voiceover generation — kie.ai ElevenLabs TTS endpoint.
"""

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

TTS_MODEL = "elevenlabs/text-to-speech-multilingual-v2"


def _kie_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('KIE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


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
        resp = await client.post(
            f"{KIE_BASE}/elevenlabs/tts",
            json=payload,
            headers=_kie_headers(),
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs TTS failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )

    content_type = resp.headers.get("content-type", "")

    if "audio" in content_type or "octet-stream" in content_type:
        # Direct binary audio response
        output_path.write_bytes(resp.content)
    else:
        # JSON response containing a URL or base64
        data = resp.json()
        audio_url = (
            data.get("audioUrl")
            or data.get("audio_url")
            or data.get("url")
            or data.get("data", {}).get("audioUrl")
            or data.get("data", {}).get("url")
        )
        audio_b64 = (
            data.get("audioBase64")
            or data.get("audio_base64")
            or data.get("audio")
        )

        if audio_url:
            async with httpx.AsyncClient(timeout=60) as client:
                dl = await client.get(audio_url)
                dl.raise_for_status()
                output_path.write_bytes(dl.content)
        elif audio_b64:
            import base64
            output_path.write_bytes(base64.b64decode(audio_b64))
        else:
            raise RuntimeError(
                f"Unrecognised TTS response format: {str(data)[:400]}"
            )

    print(f"[audio_gen] Voiceover saved → {output_path}")
    return output_path
