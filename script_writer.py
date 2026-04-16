"""
Script writer — uses Anthropic Claude to:
  1. Write a 45-60 second voiceover script for a property listing.
  2. Generate a short cinematic motion prompt for each listing image.
"""

import os
import re

import anthropic

CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Real estate motion prompts by image position (used when Claude isn't called per-image)
_DEFAULT_MOTION_PROMPTS = [
    "slow cinematic push in toward the main entrance, warm golden-hour sunlight, luxury real estate",
    "gentle rightward dolly revealing the open-plan living and dining area, soft interior lighting",
    "slow aerial-style tilt down over the kitchen and island, natural daylight streaming through windows",
    "smooth tracking shot gliding past the primary bedroom suite, warm morning light",
    "slow pan across the master bathroom with spa-like finishes, clean soft lighting",
    "gentle push in toward the outdoor terrace or pool, golden sunset reflecting on water",
    "cinematic wide reveal of the ocean or mountain view, twilight, gentle breeze effect",
    "slow orbit around the property exterior, lush landscaping, blue-sky ambiance",
]

SYSTEM_PROMPT = (
    "You are a professional real estate video narrator. "
    "Write a natural, engaging 45-60 second voiceover script for this property listing. "
    "Highlight the key selling points, location, price, and lifestyle. "
    "Keep it warm and conversational, not salesy. "
    "End with a clear call to action. "
    "Return ONLY the script text, no headers or stage directions."
)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def write_script(property_data: dict, language: str = "en") -> str:
    """Generate a voiceover script from property data."""
    lang_note = ""
    if language == "es":
        lang_note = "Write the script in Spanish (Mexican Spanish preferred).\n\n"

    user_message = f"""{lang_note}Property details:

Title: {property_data.get('title', 'N/A')}
Price: {property_data.get('price', 'N/A')}
Bedrooms: {property_data.get('beds', 'N/A')}
Bathrooms: {property_data.get('baths', 'N/A')}
Area: {property_data.get('sqft', 'N/A')}
Location: {property_data.get('location', 'N/A')}
Amenities: {', '.join(property_data.get('amenities', [])) or 'N/A'}

Description:
{property_data.get('description', '')}"""

    client = _client()
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text.strip()


async def generate_image_prompts(images: list[dict], property_data: dict) -> list[str]:
    """
    Generate a short cinematic motion prompt for each listing image.
    Uses Claude to create context-aware prompts; falls back to defaults.
    """
    n = len(images)
    if n == 0:
        return []

    # Build a single prompt describing all images by their position
    location = property_data.get("location", "luxury property")
    title = property_data.get("title", "luxury real estate")

    user_message = f"""You are writing motion prompts for a real estate video about:
"{title}" in {location}.

Write exactly {n} short cinematic motion prompts (one per line, numbered 1-{n}).
Each prompt describes camera movement for a different room/area of the property.
Use variety: slow push, dolly, orbit, tilt, pan, etc.
Keep each prompt under 20 words. Include "luxury real estate" style language.
End each with ", soft cinematic lighting".
Return ONLY the numbered list."""

    try:
        client = _client()
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = message.content[0].text.strip()
        lines = [
            re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            for line in raw.splitlines()
            if line.strip()
        ]
        # Ensure we have one prompt per image
        prompts = lines[:n]
        while len(prompts) < n:
            idx = len(prompts)
            prompts.append(
                _DEFAULT_MOTION_PROMPTS[idx % len(_DEFAULT_MOTION_PROMPTS)]
            )
        return prompts
    except Exception as exc:
        print(f"[script_writer] Could not generate image prompts via Claude: {exc}")
        # Return defaults padded to n
        return [
            _DEFAULT_MOTION_PROMPTS[i % len(_DEFAULT_MOTION_PROMPTS)]
            for i in range(n)
        ]


