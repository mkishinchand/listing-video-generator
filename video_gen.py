"""
Video clip generation — kie.ai Kling 2.6 image-to-video API.

Each listing image is animated into an 8-second clip.
Falls back to FFmpeg Ken Burns (zoompan) if the API fails for any clip.
Falls back entirely to Ken Burns if fewer than 3 clips succeed.
"""

import asyncio
import base64
import os
from pathlib import Path
from typing import Callable

import httpx

KIE_BASE = "https://api.kie.ai/api/v1"

# Endpoint map by model key (adjust if kie.ai adds new routes)
MODEL_ENDPOINTS: dict[str, str] = {
    "kling-2.6-image-to-video": "/kling/image-to-video",
    "kling-2.1-image-to-video": "/kling/image-to-video",
    "veo-3.1-fast": "/veo/image-to-video",
    "seedance-2.0": "/seedance/image-to-video",
}

CLIP_DURATION = 8        # seconds
POLL_INITIAL = 5         # seconds
POLL_MAX = 30            # seconds
TIMEOUT_MINUTES = 10


def _kie_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('KIE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


async def generate_video_clips(
    images: list[dict],
    prompts: list[str],
    job_dir: Path,
    job_id: str,
    base_url: str,
    model: str,
    aspect_ratio: str,
    on_clip_done: Callable[[int, int], None],
) -> list[Path]:
    """
    Generate one video clip per image. Returns list of paths to downloaded clips.
    Skips failed clips. Falls back to Ken Burns if too few succeed.
    """
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    total = len(images)
    successful_clips: list[Path] = []
    failed_indices: list[int] = []

    for idx, (img, prompt) in enumerate(zip(images, prompts), start=1):
        clip_path = clips_dir / f"clip_{idx:02d}.mp4"
        image_url = _build_image_url(img, job_id, base_url)

        print(f"[video_gen] Requesting clip {idx}/{total}: {img['filename']}")
        try:
            task_id = await _submit_clip(image_url, prompt, model, aspect_ratio)
            downloaded = await _poll_and_download(task_id, model, clip_path)
            if downloaded:
                successful_clips.append(clip_path)
                print(f"[video_gen] Clip {idx} done ✓")
            else:
                raise RuntimeError("Download returned empty")
        except Exception as exc:
            print(f"[video_gen] Clip {idx} failed ({exc}), will use Ken Burns fallback")
            failed_indices.append(idx)
            # Generate Ken Burns fallback immediately for this image
            kb_path = await _ken_burns_clip(
                Path(img["local_path"]), clip_path, aspect_ratio
            )
            if kb_path:
                successful_clips.append(kb_path)
                print(f"[video_gen] Clip {idx} → Ken Burns fallback ✓")

        on_clip_done(len(successful_clips) + len(failed_indices), total)

    # Final safety check: if we have very few clips, regenerate all with Ken Burns
    if len(successful_clips) < 3 and total > 0:
        print("[video_gen] Too few clips succeeded — rebuilding all with Ken Burns")
        successful_clips = []
        for idx, img in enumerate(images, start=1):
            clip_path = clips_dir / f"clip_kb_{idx:02d}.mp4"
            kb = await _ken_burns_clip(
                Path(img["local_path"]), clip_path, aspect_ratio
            )
            if kb:
                successful_clips.append(kb)

    return successful_clips


def _build_image_url(img: dict, job_id: str, base_url: str) -> str:
    """
    Prefer original CDN URL; fall back to locally-served URL.
    kie.ai must be able to reach this URL from its servers.
    """
    original = img.get("original_url", "")
    if original and original.startswith("http"):
        return original
    # Serve locally (requires BASE_URL to be publicly reachable)
    filename = img["filename"]
    return f"{base_url.rstrip('/')}/job-images/{job_id}/{filename}"


async def _submit_clip(
    image_url: str,
    prompt: str,
    model: str,
    aspect_ratio: str,
) -> str:
    """POST to kie.ai and return the taskId."""
    endpoint_path = MODEL_ENDPOINTS.get(model, "/kling/image-to-video")

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "duration": CLIP_DURATION,
        "aspectRatio": aspect_ratio,
        "waterMark": False,
        "callBackUrl": None,
    }

    # Try URL; if it's a localhost URL encode image as base64 instead
    if "localhost" in image_url or "127.0.0.1" in image_url:
        local_path = _local_path_from_url(image_url)
        if local_path and local_path.exists():
            payload["imageBase64"] = _to_base64(local_path)
        else:
            payload["imageUrl"] = image_url
    else:
        payload["imageUrl"] = image_url

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{KIE_BASE}{endpoint_path}",
            json=payload,
            headers=_kie_headers(),
        )

    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Submit failed: HTTP {resp.status_code} — {resp.text[:400]}")

    data = resp.json()
    task_id = (
        data.get("taskId")
        or data.get("task_id")
        or data.get("id")
        or (data.get("data") or {}).get("taskId")
        or (data.get("data") or {}).get("task_id")
    )
    if not task_id:
        raise RuntimeError(f"No taskId in response: {str(data)[:400]}")

    print(f"[video_gen] Task submitted: {task_id}")
    return str(task_id)


async def _poll_and_download(
    task_id: str,
    model: str,
    output_path: Path,
) -> Path | None:
    """Poll until completed, then download the clip."""
    endpoint_path = MODEL_ENDPOINTS.get(model, "/kling/image-to-video")
    poll_url = f"{KIE_BASE}{endpoint_path}/{task_id}"

    wait = POLL_INITIAL
    elapsed = 0
    max_seconds = TIMEOUT_MINUTES * 60

    while elapsed < max_seconds:
        await asyncio.sleep(wait)
        elapsed += wait

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(poll_url, headers=_kie_headers())
            resp.raise_for_status()
        except Exception as exc:
            print(f"[video_gen] Poll error ({exc}), retrying…")
            wait = min(wait + 5, POLL_MAX)
            continue

        data = resp.json()
        inner = data.get("data") or data
        status = (inner.get("status") or "").lower()
        print(f"[video_gen] Task {task_id} status: {status}")

        if status in ("completed", "succeed", "success", "done"):
            video_url = (
                inner.get("videoUrl")
                or inner.get("video_url")
                or inner.get("url")
                or (inner.get("output") or {}).get("video_url")
                or (inner.get("output") or {}).get("videoUrl")
            )
            if not video_url:
                raise RuntimeError(f"No videoUrl in completed response: {str(data)[:400]}")

            async with httpx.AsyncClient(timeout=120) as dl_client:
                dl = await dl_client.get(video_url, follow_redirects=True)
            dl.raise_for_status()
            output_path.write_bytes(dl.content)
            return output_path

        elif status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Task {task_id} ended with status: {status}")

        # Still processing — back off
        wait = min(wait + 5, POLL_MAX)

    raise TimeoutError(f"Task {task_id} timed out after {TIMEOUT_MINUTES} minutes")


# ── Ken Burns (zoompan) fallback ─────────────────────────────────────────────
async def _ken_burns_clip(
    image_path: Path,
    output_path: Path,
    aspect_ratio: str,
) -> Path | None:
    """Generate a static-image clip with a slow zoom/pan effect."""
    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    fps = 25
    frames = CLIP_DURATION * fps  # 200 frames

    # Alternate zoom direction per clip for variety
    zoom_expr = "min(zoom+0.0008,1.25)"
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    vf = (
        f"scale={width * 2}:{height * 2},"
        f"zoompan=z='{zoom_expr}':d={frames}:x='{x_expr}':y='{y_expr}',"
        f"scale={width}:{height},"
        f"setsar=1"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", vf,
        "-t", str(CLIP_DURATION),
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        str(output_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            print(f"[video_gen] Ken Burns ffmpeg error: {stderr.decode()[-500:]}")
            return None
        return output_path
    except Exception as exc:
        print(f"[video_gen] Ken Burns failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _to_base64(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/{mime};base64,{b64}"


def _local_path_from_url(url: str) -> Path | None:
    """
    Convert a job-images URL back to a local Path.
    e.g. http://localhost:8000/job-images/abc123/image_01.jpg
         → jobs/abc123/images/image_01.jpg
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        # parts = ["job-images", job_id, filename]
        if len(parts) == 3 and parts[0] == "job-images":
            return Path("jobs") / parts[1] / "images" / parts[2]
    except Exception:
        pass
    return None
