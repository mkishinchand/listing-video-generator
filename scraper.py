"""
Listing scraper — Playwright (JS-rendered) + BeautifulSoup4.

Targets: findmexicohouses.com / Ronival listings.
Falls back gracefully to any standard real estate page.
"""

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

MAX_IMAGES = 8
TIMEOUT_MS = 30_000


async def scrape_listing(url: str, job_dir: Path) -> tuple[dict, list[dict]]:
    """
    Scrape a real estate listing page.

    Returns:
        property_data   dict with title, price, beds, baths, sqft, description,
                        location, amenities
        images          list of {"filename": ..., "original_url": ...,
                                 "local_path": ...}
    """
    images_dir = job_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    html, page_url = await _fetch_with_playwright(url)
    property_data, image_urls = _parse_listing(html, page_url)

    if not image_urls:
        raise RuntimeError(
            "No images found on the listing page. "
            "The site may block scrapers — use the manual upload form."
        )

    # Download images (up to MAX_IMAGES)
    images = await _download_images(image_urls[:MAX_IMAGES], images_dir)

    if not images:
        raise RuntimeError(
            "Could not download any listing images. "
            "Check network access and try the manual upload form."
        )

    return property_data, images


# ── Playwright fetch ─────────────────────────────────────────────────────────
async def _fetch_with_playwright(url: str) -> tuple[str, str]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.0 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
                locale="en-US",
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=TIMEOUT_MS)

            # Scroll to trigger lazy-loaded images
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)

            html = await page.content()
            final_url = page.url
        finally:
            await browser.close()

    return html, final_url


# ── HTML parsing ─────────────────────────────────────────────────────────────
def _parse_listing(html: str, page_url: str) -> tuple[dict, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    base_domain = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    property_data = {
        "title": _extract_title(soup),
        "price": _extract_price(soup),
        "beds": _extract_stat(soup, ["bed", "bedroom", "recámara", "recamara"]),
        "baths": _extract_stat(soup, ["bath", "bathroom", "baño", "bano"]),
        "sqft": _extract_area(soup),
        "description": _extract_description(soup),
        "location": _extract_location(soup, page_url),
        "amenities": _extract_amenities(soup),
        "url": page_url,
    }

    image_urls = _extract_images(soup, page_url, base_domain)
    return property_data, image_urls


def _extract_title(soup: BeautifulSoup) -> str:
    for sel in [
        "h1.property-title",
        "h1.listing-title",
        ".property-detail h1",
        ".listing-detail h1",
        "h1",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)

    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else "Property Listing"


def _extract_price(soup: BeautifulSoup) -> str:
    for sel in [
        ".property-price",
        ".listing-price",
        ".price",
        '[class*="price"]',
        '[itemprop="price"]',
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if re.search(r"[\$€£¥]|\d", text):
                return text

    # Search all text for price patterns
    for el in soup.find_all(string=re.compile(r"\$[\d,\.]+|\d[\d,\.]+\s*(USD|MXN|usd)")):
        text = el.strip()
        if text:
            return text

    return "Contact for Price"


def _extract_stat(soup: BeautifulSoup, keywords: list[str]) -> str:
    pattern = re.compile("|".join(keywords), re.IGNORECASE)

    for el in soup.find_all(True):
        text = el.get_text(strip=True)
        if pattern.search(text) and re.search(r"\d", text) and len(text) < 80:
            nums = re.findall(r"\d+\.?\d*", text)
            if nums:
                return nums[0]

    return "N/A"


def _extract_area(soup: BeautifulSoup) -> str:
    area_pattern = re.compile(
        r"(\d[\d,\.]*)\s*(sqft|sq\.?\s*ft|m²|m2|metros?|square)", re.IGNORECASE
    )
    for el in soup.find_all(string=area_pattern):
        m = area_pattern.search(el)
        if m:
            return m.group(0).strip()

    for sel in [".property-area", ".area", '[class*="sqft"]', '[class*="size"]']:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if re.search(r"\d", text):
                return text

    return "N/A"


def _extract_description(soup: BeautifulSoup) -> str:
    for sel in [
        ".property-description",
        ".listing-description",
        ".description",
        '[class*="description"]',
        '[itemprop="description"]',
        "article",
        ".property-detail p",
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 50:
                return text[:2000]

    # Fallback: largest <p> block
    paragraphs = sorted(
        (p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 80),
        key=len,
        reverse=True,
    )
    return paragraphs[0][:2000] if paragraphs else "Luxurious property in a prime location."


def _extract_location(soup: BeautifulSoup, page_url: str) -> str:
    for sel in [
        ".property-location",
        ".listing-location",
        ".address",
        '[class*="location"]',
        '[itemprop="address"]',
        ".property-address",
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    # Try meta / title hints
    meta = soup.find("meta", {"property": "og:title"}) or soup.find("title")
    if meta:
        content = meta.get("content", "") or meta.get_text()
        parts = re.split(r"[|\-–,]", content)
        if len(parts) >= 2:
            return parts[-1].strip()

    parsed = urlparse(page_url)
    return parsed.netloc.replace("www.", "")


def _extract_amenities(soup: BeautifulSoup) -> list[str]:
    amenities: list[str] = []

    for sel in [
        ".amenities",
        ".features",
        ".property-features",
        '[class*="amenities"]',
        '[class*="features"]',
        "ul.features",
        "ul.amenities",
    ]:
        container = soup.select_one(sel)
        if container:
            items = container.find_all("li")
            if items:
                amenities = [li.get_text(strip=True) for li in items if li.get_text(strip=True)]
                break

    return amenities[:20]


def _extract_images(
    soup: BeautifulSoup, page_url: str, base_domain: str
) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []

    # Common gallery containers (prioritised order)
    gallery_selectors = [
        ".property-gallery img",
        ".listing-gallery img",
        ".gallery img",
        ".slider img",
        ".carousel img",
        '[class*="gallery"] img',
        '[class*="slider"] img',
        ".property-images img",
    ]

    candidates: list[Any] = []
    for sel in gallery_selectors:
        found = soup.select(sel)
        if found:
            candidates.extend(found)
            break

    # Fallback — all images on page
    if not candidates:
        candidates = soup.find_all("img")

    for img in candidates:
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("data-original")
            or img.get("data-full")
        )
        if not src:
            continue

        # Resolve relative URLs
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = base_domain + src
        elif not src.startswith("http"):
            src = urljoin(page_url, src)

        # Skip tiny/icon images
        if any(skip in src.lower() for skip in ["logo", "icon", "avatar", "placeholder", "blank"]):
            continue
        if src in seen:
            continue

        # Check declared size attrs
        width = img.get("width", "")
        height = img.get("height", "")
        try:
            if int(width) < 200 or int(height) < 150:
                continue
        except (ValueError, TypeError):
            pass

        seen.add(src)
        urls.append(src)

    return urls


# ── Image downloader ─────────────────────────────────────────────────────────
async def _download_images(urls: list[str], images_dir: Path) -> list[dict]:
    results: list[dict] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.findmexicohouses.com/",
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        tasks = [
            _download_one(client, url, images_dir, idx)
            for idx, url in enumerate(urls, start=1)
        ]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)

    for item in fetched:
        if isinstance(item, dict):
            results.append(item)

    return results


async def _download_one(
    client: httpx.AsyncClient, url: str, images_dir: Path, idx: int
) -> dict:
    ext = _guess_extension(url)
    filename = f"image_{idx:02d}{ext}"
    local_path = images_dir / filename

    try:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and "octet-stream" not in content_type:
            raise ValueError(f"Unexpected content-type: {content_type}")

        local_path.write_bytes(resp.content)
        return {
            "filename": filename,
            "original_url": url,
            "local_path": str(local_path),
        }
    except Exception as exc:
        print(f"[scraper] Failed to download image {idx} ({url}): {exc}")
        raise


async def build_images_from_urls(urls: list[str], job_dir: Path) -> list[dict]:
    """Download images from explicit URLs (manual upload flow)."""
    images_dir = job_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return await _download_images(urls, images_dir)


def _guess_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ext if ext != ".jpeg" else ".jpg"
    return ".jpg"
