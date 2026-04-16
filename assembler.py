"""
FFmpeg assembly — concatenate clips, mix audio, add text overlays,
fade in/out, and export a web-optimised H.264/AAC MP4.
"""

import asyncio
import json
import math
import re
import sys
from pathlib import Path

MUSIC_PATH = Path("music/background.mp3")
FPS = 25
FADE_DURATION = 1  # seconds


def _detect_font() -> str:
    """Return an absolute font path without spaces that works on the current OS."""
    if sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Geneva.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    elif sys.platform == "win32":
        candidates = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]

    for path in candidates:
        if Path(path).exists():
            return path

    # Fallback: signal to use fontconfig name instead of file path
    return ""


def _esc(text: str) -> str:
    """Escape text for FFmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    # Keep only printable ASCII to avoid font encoding issues
    text = re.sub(r"[^\x20-\x7E]", "", text)
    return text[:80]


async def _get_audio_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    info = json.loads(stdout.decode())
    for stream in info.get("streams", []):
        dur = stream.get("duration")
        if dur:
            return float(dur)
    return 45.0  # fallback


async def assemble_video(
    clips: list[Path],
    voiceover_path: Path,
    job_dir: Path,
    property_data: dict,
    agent_name: str,
    aspect_ratio: str,
) -> Path:
    """
    1. Measure voiceover duration.
    2. Pad/loop clips to cover the voiceover.
    3. Concatenate clips.
    4. Mix voiceover + background music.
    5. Add text overlays (title → price → stats → branding).
    6. Fade in/out and encode final video.
    """
    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    vo_duration = await _get_audio_duration(voiceover_path)
    print(f"[assembler] Voiceover duration: {vo_duration:.1f}s")

    # Pad clips list to cover full voiceover
    clips_needed = math.ceil(vo_duration / 8)
    padded_clips: list[Path] = []
    while len(padded_clips) < clips_needed:
        padded_clips.extend(clips)
    padded_clips = padded_clips[:clips_needed]

    # Write concat list
    concat_file = job_dir / "concat_list.txt"
    with concat_file.open("w") as f:
        for clip in padded_clips:
            f.write(f"file '{clip.resolve()}'\n")

    # Concatenate clips
    concat_output = job_dir / "concatenated.mp4"
    await _run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(concat_output),
    ], "concat")

    # Get total video duration (clip side)
    video_duration = len(padded_clips) * 8
    final_duration = min(vo_duration + 1, video_duration)  # slight buffer

    # Build text overlay filter chains
    font = _detect_font()
    title_text = _esc(property_data.get("title", "Property for Sale"))
    price_text = _esc(property_data.get("price", ""))
    beds = property_data.get("beds", "N/A")
    baths = property_data.get("baths", "N/A")
    sqft = property_data.get("sqft", "N/A")
    stats_text = _esc(f"{beds} Bed | {baths} Bath | {sqft}")
    brand_text = _esc(agent_name) if agent_name else ""

    fade_out_start = max(final_duration - FADE_DURATION, 0)
    brand_start = max(final_duration - 3, 0)

    # drawtext chains — font size scales with resolution
    fs_large = int(height * 0.045)
    fs_medium = int(height * 0.032)
    fs_small = int(height * 0.024)
    pad = int(height * 0.04)

    shadow = "shadowx=2:shadowy=2:shadowcolor=black@0.7"
    box = "box=1:boxcolor=black@0.4:boxborderw=8"

    def dt(text: str, size: int, x: str, y: str, t_start: float, t_end: float) -> str:
        # Commas inside single-quoted enable expression are fine for FFmpeg's parser
        enable = f"between(t,{t_start:.2f},{t_end:.2f})"
        font_part = f"fontfile='{font}'" if font else "font=sans"
        parts = [
            f"drawtext={font_part}",
            f"text='{text}'",
            f"fontsize={size}",
            f"fontcolor=white",
            f"x={x}",
            f"y={y}",
            f"enable='{enable}'",
            shadow,
            box,
        ]
        return ":".join(parts)

    y_base = f"h-{pad + fs_large}"
    overlay_parts = [dt(title_text, fs_large, str(pad), y_base, 0, 3)]
    if price_text:
        overlay_parts.append(dt(price_text, fs_medium, str(pad), f"h-{pad + fs_medium}", 3, 6))
    if stats_text:
        overlay_parts.append(dt(stats_text, fs_small, str(pad), f"h-{pad + fs_small}", 6, 9))
    if brand_text:
        overlay_parts.append(dt(brand_text, fs_small, f"w-tw-{pad}", f"h-{pad + fs_small}", brand_start, final_duration))
    overlay_chain = ",".join(overlay_parts)

    # Fade in/out on video
    fade_filter = (
        f"fade=t=in:st=0:d={FADE_DURATION},"
        f"fade=t=out:st={fade_out_start:.2f}:d={FADE_DURATION}"
    )

    video_filter = f"{overlay_chain},{fade_filter}" if overlay_chain else fade_filter

    # Audio filter: mix voiceover (-3 dB) + music (-25 dB)
    has_music = MUSIC_PATH.exists()
    if has_music:
        audio_filter = (
            "[1:a]volume=-3dB,apad[voice];"
            "[2:a]volume=-25dB,aloop=loop=-1:size=2000000000[music];"
            "[voice][music]amix=inputs=2:duration=first[audio]"
        )
        music_input = ["-i", str(MUSIC_PATH)]
        audio_map = "[audio]"
    else:
        audio_filter = "[1:a]volume=-3dB,apad[audio]"
        music_input = []
        audio_map = "[audio]"

    final_output = job_dir / "final_video.mp4"

    filter_complex = f"[0:v]{video_filter}[video];{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(concat_output),
        "-i", str(voiceover_path),
        *music_input,
        "-filter_complex", filter_complex,
        "-map", "[video]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-t", str(final_duration + FADE_DURATION),
        str(final_output),
    ]

    await _run_ffmpeg(cmd, "final assembly")
    print(f"[assembler] Final video → {final_output}")
    return final_output


async def _run_ffmpeg(cmd: list[str], label: str):
    """Run an FFmpeg command and raise on failure."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg {label} failed (exit {proc.returncode}):\n"
            + stderr.decode()[-1000:]
        )
    print(f"[assembler] FFmpeg {label} ✓")
