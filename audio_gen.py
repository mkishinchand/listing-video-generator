"""
Voiceover generation.

Primary: edge-tts (Microsoft Edge TTS — free, no API key needed, great quality).
Optional: kie.ai ElevenLabs (set KIE_TTS_ENDPOINT env var if your plan supports it).
"""

import asyncio
import base64
import os
from pathlib import Path

import httpx

KIE_BASE = "https://api.kie.ai/api/v1"

# Map UI voice names → edge-tts voice strings
EDGE_VOICES: dict[str, str] = {
    "Rachel": "en-US-AriaNeural",       # warm, female
    "Antoni": "en-US-GuyNeural",        # smooth, male
    "Bella":  "en-US-JennyNeural",      # soft, female
    "Josh":   "en-US-ChristopherNeural",# deep, male
    "Elli":   "en-US-AnaNeural",        # youthful, female
}

# Spanish equivalents (used when language == "es")
EDGE_VOICES_ES: dict[str, str] = {
    "Rachel": "es-MX-DaliaNeural",
    "Antoni": "es-MX-JorgeNeural",
    "Bella":  "es-ES-ElviraNeural",
    "Josh":   "es-ES-AlvaroNeural",
    "Elli":   "es-MX-DaliaNeural",
}

# ElevenLabs voice IDs (only used if KIE_TTS_ENDPOINT is configured)
ELEVENLABS_VOICE_IDS: dict[str, str] = {
    "Rachel": "21m00Tcm4TlvDq8ikWAM",
    "Antoni": "ErXwobaYiN019PkySvjV",
    "Bella":  "EXAVITQu4vr4xnSDxMaL",
    "Josh":   "TxGEqnHWrfWFTfGW9XjX",
    "Elli":   "MF3mGyEYCl7XYWbV9V6O",
}


async def generate_voiceover(
    text: str,
    job_dir: Path,
    voice: str = "Rachel",
    language: str = "en",
) -> Path:
    """Generate voiceover MP3 and save to job_dir/voiceover.mp3."""
    output_path = job_dir / "voiceover.mp3"

    # If user has configured a kie.ai TTS endpoint, try it first
    kie_endpoint = os.getenv("KIE_TTS_ENDPOINT", "").strip()
    if kie_endpoint:
        try:
            return await _generate_kie(text, voice, output_path, kie_endpoint)
        except Exception as exc:
            print(f"[audio_gen] kie.ai TTS failed ({exc}), falling back to edge-tts")

    # Default: edge-tts (free, no key needed)
    return await _generate_edge(text, voice, language, output_path)


# ── edge-tts ─────────────────────────────────────────────────────────────────
async def _generate_edge(
    text: str,
    voice: str,
    language: str,
    output_path: Path,
) -> Path:
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "edge-tts not installed. Run: pip install edge-tts"
        )

    voice_map = EDGE_VOICES_ES if language == "es" else EDGE_VOICES
    edge_voice = voice_map.get(voice, EDGE_VOICES["Rachel"])

    print(f"[audio_gen] edge-tts voice: {edge_voice}")
    communicate = edge_tts.Communicate(text, edge_voice)
    await communicate.save(str(output_path))
    print(f"[audio_gen] Voiceover saved → {output_path}")
    return output_path


# ── kie.ai ElevenLabs (optional) ─────────────────────────────────────────────
def _kie_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('KIE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


async def _generate_kie(
    text: str,
    voice: str,
    output_path: Path,
    endpoint_path: str,
) -> Path:
    voice_id = ELEVENLABS_VOICE_IDS.get(voice, ELEVENLABS_VOICE_IDS["Rachel"])
    payload = {
        "text": text,
        "model_id": "elevenlabs/text-to-speech-multilingual-v2",
        "voice_id": voice_id,
    }
    url = f"{KIE_BASE}{endpoint_path}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload, headers=_kie_headers())

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {resp.status_code} — {resp.text[:300]}")

    content_type = resp.headers.get("content-type", "")
    if "audio" in content_type or "octet-stream" in content_type:
        output_path.write_bytes(resp.content)
    else:
        data = resp.json()
        audio_url = (
            data.get("audioUrl") or data.get("audio_url") or data.get("url")
            or (data.get("data") or {}).get("audioUrl")
        )
        audio_b64 = data.get("audioBase64") or data.get("audio_base64") or data.get("audio")
        if audio_url:
            async with httpx.AsyncClient(timeout=60) as dl:
                r = await dl.get(audio_url, follow_redirects=True)
                r.raise_for_status()
                output_path.write_bytes(r.content)
        elif audio_b64:
            output_path.write_bytes(base64.b64decode(audio_b64))
        else:
            raise RuntimeError(f"Unrecognised TTS response: {str(data)[:300]}")

    return output_path
