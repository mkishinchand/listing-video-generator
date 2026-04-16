# Listing Video Generator

Paste a property listing URL → get a cinematic marketing video.

**Pipeline:** Playwright scrape → Claude script → ElevenLabs voiceover → Kling 2.6 image-to-video → FFmpeg assembly.

---

## Prerequisites

### 1. FFmpeg (required)

**macOS**
```bash
brew install ffmpeg
```

**Ubuntu / Debian**
```bash
sudo apt-get install ffmpeg
```

**Windows**
Download from https://ffmpeg.org/download.html and add to PATH.

Verify: `ffmpeg -version`

---

### 2. Python 3.10+

---

### 3. API Keys

You need:
- **Anthropic API key** — https://console.anthropic.com
- **kie.ai API key** — https://kie.ai (covers both Kling video generation and ElevenLabs TTS)

---

## Setup

```bash
# 1. Clone / navigate to project
cd listing-video-generator

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install Python deps
pip install -r requirements.txt

# 4. Install Playwright browser (Chromium)
playwright install chromium

# 5. Copy and fill in .env
cp .env.example .env
# Edit .env with your API keys

# 6. Add background music (optional but recommended)
# Download a royalty-free ambient/piano track and save as:
#   music/background.mp3
#
# Suggested sources:
#   - https://www.bensound.com/royalty-free-music/track/sweet  (requires attribution)
#   - https://freemusicarchive.org/genre/Ambient/
#   - https://pixabay.com/music/search/real%20estate/
#
# If music/background.mp3 is absent, the video will use voiceover only.

# 7. Start the server
uvicorn main:app --reload
# → open http://localhost:8000
```

---

## Public Image URLs (for kie.ai)

kie.ai's Kling API needs to fetch your listing images from a **publicly accessible URL**.

**For local development:**
The app first tries the listing site's original CDN URLs. If those are restricted, you need a tunnel:

```bash
# Option A — ngrok (free tier)
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL and add to .env:
# BASE_URL=https://xxxx.ngrok.io

# Option B — cloudflared (free)
cloudflared tunnel --url http://localhost:8000
```

**For server deployment:**
Set `BASE_URL=https://yourdomain.com` in `.env` — local serving works automatically.

---

## Docker

```bash
# Build
docker build -t listing-video-gen .

# Run
docker run -p 8000:8000 \
  -e KIE_API_KEY=your_key \
  -e ANTHROPIC_API_KEY=your_key \
  -e BASE_URL=https://yourdomain.com \
  -v $(pwd)/jobs:/app/jobs \
  -v $(pwd)/music:/app/music \
  listing-video-gen
```

---

## Usage

1. Open http://localhost:8000
2. Paste a listing URL (works best with **findmexicohouses.com / Ronival** listings)
3. Optionally adjust settings (voice, video format, branding name)
4. Click **Generate Video**
5. Review and optionally edit the voiceover script
6. Click **Generate Video** → watch progress
7. Download your MP4

---

## Fallbacks

| Scenario | Behaviour |
|---|---|
| Site blocks Playwright | Manual form appears — enter details + image URLs by hand |
| A Kling clip fails | That clip is replaced with a Ken Burns (zoom/pan) static image effect |
| Fewer than 3 Kling clips succeed | Entire video rebuilt with Ken Burns on all images |
| No `music/background.mp3` | Video renders with voiceover only (no crash) |

---

## Project Structure

```
listing-video-generator/
├── main.py           FastAPI app + job management
├── scraper.py        Playwright + BeautifulSoup4 scraper
├── script_writer.py  Claude API — script + image prompts
├── audio_gen.py      kie.ai ElevenLabs TTS
├── video_gen.py      kie.ai Kling image-to-video + polling
├── assembler.py      FFmpeg concat / mix / overlays / encode
├── static/
│   └── index.html    Single-page UI
├── jobs/             Job state & output files (auto-created)
├── music/
│   └── background.mp3  ← add your own royalty-free track
├── requirements.txt
├── .env.example
├── Dockerfile
└── README.md
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `KIE_API_KEY` | Yes | kie.ai API key |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `BASE_URL` | No | Public URL for image serving (default: `http://localhost:8000`) |

---

## Video Models (via kie.ai)

| UI option | Model ID |
|---|---|
| Kling 2.6 *(recommended)* | `kling-2.6-image-to-video` |
| Kling 2.1 | `kling-2.1-image-to-video` |
| Veo 3.1 Fast | `veo-3.1-fast` |
| Seedance 2.0 | `seedance-2.0` |

---

## Supported Voices (ElevenLabs via kie.ai)

| Name | Style |
|---|---|
| Rachel | Warm, female — great for luxury real estate |
| Antoni | Smooth, male |
| Bella | Soft, female |
| Josh | Deep, male |
| Elli | Youthful, female |
