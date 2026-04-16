"""
Listing Video Generator — FastAPI backend
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Listing Video Generator")

JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

# ── Static files ────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/job-images/{job_id}/{filename}")
async def serve_job_image(job_id: str, filename: str):
    """Serve a downloaded listing image so kie.ai can fetch it."""
    image_path = JOBS_DIR / job_id / "images" / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)


# ── Pydantic models ─────────────────────────────────────────────────────────
class ManualData(BaseModel):
    title: str = ""
    price: str = ""
    location: str = ""
    beds: str = ""
    baths: str = ""
    sqft: str = ""
    description: str = ""
    image_urls: list[str] = []


class GenerateRequest(BaseModel):
    url: str
    language: str = "en"
    agent_name: str = "Ronival Real Estate"
    aspect_ratio: str = "16:9"
    video_model: str = "kling-2.6-image-to-video"
    voice: str = "Rachel"
    manual_data: ManualData | None = None


class ContinueRequest(BaseModel):
    script: str


# ── Job helpers ──────────────────────────────────────────────────────────────
def get_job(job_id: str) -> dict:
    job_file = JOBS_DIR / job_id / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return json.loads(job_file.read_text())


def save_job(job_id: str, data: dict):
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2))


# ── API endpoints ────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/generate")
async def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex[:8]
    job_data = {
        "job_id": job_id,
        "status": "starting",
        "step": 1,
        "progress": 0,
        "message": "Starting…",
        "url": request.url,
        "language": request.language,
        "agent_name": request.agent_name,
        "aspect_ratio": request.aspect_ratio,
        "video_model": request.video_model,
        "voice": request.voice,
        "manual_data": request.manual_data.model_dump() if request.manual_data else None,
        "script": None,
        "property_data": None,
        "images": [],
        "clips": [],
        "clips_done": 0,
        "clips_total": 0,
        "voiceover_path": None,
        "final_video_path": None,
        "error": None,
    }
    save_job(job_id, job_data)
    background_tasks.add_task(run_scrape_and_script, job_id)
    return {"job_id": job_id}


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return get_job(job_id)


@app.post("/continue/{job_id}")
async def continue_job(
    job_id: str, request: ContinueRequest, background_tasks: BackgroundTasks
):
    job = get_job(job_id)
    if job["status"] != "waiting_for_script":
        raise HTTPException(
            status_code=400, detail="Job is not waiting for script confirmation"
        )
    job["script"] = request.script
    job["status"] = "starting_voiceover"
    save_job(job_id, job)
    background_tasks.add_task(run_voiceover_and_video, job_id)
    return {"status": "ok"}


@app.get("/download/{job_id}")
async def download(job_id: str):
    job = get_job(job_id)
    if job["status"] != "completed" or not job.get("final_video_path"):
        raise HTTPException(status_code=404, detail="Video not ready")
    video_path = Path(job["final_video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=f"listing_video_{job_id}.mp4",
    )


# ── Background pipeline ──────────────────────────────────────────────────────
async def run_scrape_and_script(job_id: str):
    """Phase 1: scrape the listing page (or use manual data) and write the script."""
    from scraper import scrape_listing, build_images_from_urls
    from script_writer import write_script

    job = get_job(job_id)
    job_dir = JOBS_DIR / job_id

    try:
        manual = job.get("manual_data")

        if manual and manual.get("title"):
            # Manual data path — skip scraping
            property_data = {
                "title": manual.get("title", ""),
                "price": manual.get("price", ""),
                "beds": manual.get("beds", ""),
                "baths": manual.get("baths", ""),
                "sqft": manual.get("sqft", ""),
                "description": manual.get("description", ""),
                "location": manual.get("location", ""),
                "amenities": [],
                "url": job.get("url", ""),
            }
            job.update(
                {
                    "status": "scraping",
                    "step": 1,
                    "progress": 5,
                    "message": "Downloading images…",
                }
            )
            save_job(job_id, job)

            images = await build_images_from_urls(
                manual.get("image_urls", [])[:8], job_dir
            )
        else:
            # Scrape path
            job.update(
                {
                    "status": "scraping",
                    "step": 1,
                    "progress": 5,
                    "message": "Scraping listing…",
                }
            )
            save_job(job_id, job)

            property_data, images = await scrape_listing(job["url"], job_dir)

        job.update(
            {
                "property_data": property_data,
                "images": images,
                "progress": 40,
                "message": f"Got {len(images)} image(s)",
            }
        )
        save_job(job_id, job)

        # Step 2 — Write script
        job.update(
            {
                "status": "scripting",
                "step": 2,
                "progress": 55,
                "message": "Writing voiceover script…",
            }
        )
        save_job(job_id, job)

        script = await write_script(property_data, job.get("language", "en"))

        job.update(
            {
                "status": "waiting_for_script",
                "script": script,
                "step": 2,
                "progress": 100,
                "message": "Script ready — review and continue",
            }
        )
        save_job(job_id, job)

    except Exception as exc:
        job = get_job(job_id)
        job.update({"status": "failed", "error": str(exc)})
        save_job(job_id, job)
        raise


async def run_voiceover_and_video(job_id: str):
    """Phase 2: generate voiceover → video clips → assemble."""
    from audio_gen import generate_voiceover
    from video_gen import generate_video_clips
    from assembler import assemble_video
    from script_writer import generate_image_prompts

    job = get_job(job_id)
    job_dir = JOBS_DIR / job_id

    try:
        # Step 3 — Voiceover
        job.update(
            {
                "status": "voiceover",
                "step": 3,
                "progress": 10,
                "message": "Generating voiceover…",
            }
        )
        save_job(job_id, job)

        voiceover_path = await generate_voiceover(
            text=job["script"],
            job_dir=job_dir,
            voice=job.get("voice", "Rachel"),
        )

        job.update(
            {
                "voiceover_path": str(voiceover_path),
                "progress": 30,
                "message": "Voiceover done",
            }
        )
        save_job(job_id, job)

        # Step 4 — Image motion prompts + video clip generation
        images = job["images"]
        property_data = job["property_data"]

        image_prompts = await generate_image_prompts(images, property_data)

        job.update(
            {
                "status": "generating_clips",
                "step": 4,
                "progress": 35,
                "message": f"Animating 0/{len(images)} images…",
                "clips_done": 0,
                "clips_total": len(images),
            }
        )
        save_job(job_id, job)

        base_url = os.getenv("BASE_URL", "http://localhost:8000")

        def on_clip_done(done: int, total: int):
            try:
                j = get_job(job_id)
                j.update(
                    {
                        "clips_done": done,
                        "clips_total": total,
                        "progress": 35 + int((done / total) * 38),
                        "message": f"Animating {done}/{total} images…",
                    }
                )
                save_job(job_id, j)
            except Exception:
                pass

        clips = await generate_video_clips(
            images=images,
            prompts=image_prompts,
            job_dir=job_dir,
            job_id=job_id,
            base_url=base_url,
            model=job.get("video_model", "kling-2.6-image-to-video"),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
            on_clip_done=on_clip_done,
        )

        if not clips:
            raise RuntimeError("No video clips were generated successfully.")

        job = get_job(job_id)
        job.update(
            {
                "clips": [str(c) for c in clips],
                "progress": 78,
                "message": f"Generated {len(clips)} clip(s), assembling…",
            }
        )
        save_job(job_id, job)

        # Step 5 — FFmpeg assembly
        job.update(
            {
                "status": "assembling",
                "step": 5,
                "progress": 80,
                "message": "Assembling final video…",
            }
        )
        save_job(job_id, job)

        final_path = await assemble_video(
            clips=[Path(c) for c in clips],
            voiceover_path=Path(voiceover_path),
            job_dir=job_dir,
            property_data=property_data,
            agent_name=job.get("agent_name", ""),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
        )

        job.update(
            {
                "status": "completed",
                "step": 5,
                "progress": 100,
                "message": "Video ready!",
                "final_video_path": str(final_path),
            }
        )
        save_job(job_id, job)

    except Exception as exc:
        job = get_job(job_id)
        job.update({"status": "failed", "error": str(exc)})
        save_job(job_id, job)
        raise


# ── Dev entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
