"""
In-Memory Screen Vision and Debugging for Vega 2.0.
Captures active display directly into memory (zero disk writes), downscaling to max 1080p
for fast inference with Gemini / vision models.
"""

import io
import os
import base64
from PIL import ImageGrab, Image
from tools.registry import tools


def capture_screen_jpeg(max_dim: int = 1920, quality: int = 80) -> bytes:
    """Captures primary screen and returns compressed JPEG bytes in memory."""
    img = ImageGrab.grab()
    width, height = img.size

    # Downscale if larger than max_dim
    if max(width, height) > max_dim:
        scale = max_dim / float(max(width, height))
        new_size = (int(width * scale), int(height * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    # Convert RGBA to RGB for JPEG compatibility
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


@tools.register(
    name="inspect_active_screen",
    description="Captures the active PC screen into memory and analyzes terminal output, errors, or open windows to answer the user's question.",
    parameters={
        "type": "object",
        "properties": {
            "user_question": {
                "type": "string",
                "description": "What the user wants to know about what is currently shown on their screen"
            }
        },
        "required": ["user_question"]
    }
)
def inspect_active_screen(user_question: str) -> str:
    """Captures screen and sends to Gemini multimodal vision API if configured."""
    try:
        jpeg_bytes = capture_screen_jpeg()
        b64_data = base64.b64encode(jpeg_bytes).decode("utf-8")
        size_kb = len(jpeg_bytes) // 1024

        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if gemini_key:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"You are Vega, a developer assistant. The user is asking about their screen: {user_question}. Analyze the image precisely and explain step-by-step."},
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": b64_data
                                }
                            }
                        ]
                    }
                ]
            }
            res = requests.post(url, json=payload, timeout=25)
            if res.ok:
                data = res.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                return f"Captured {size_kb} KB screen buffer, but vision API error: {res.status_code} {res.text[:150]}"

        return f"Captured {size_kb} KB screen buffer in-memory. (Configure GEMINI_API_KEY in Settings to enable automated multimodal diagnosis)."
    except Exception as exc:
        return f"Screen vision failed: {exc}"

