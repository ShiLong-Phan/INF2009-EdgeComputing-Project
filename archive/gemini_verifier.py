from typing import Optional, Tuple

import PIL.Image
from google import genai


PROMPT = (
    "Look at this image carefully.\\n"
    "Is the main object in the image a BOTTLE or a CAN?\\n\\n"
    "Definitions:\\n"
    "  BOTTLE - any bottle made of glass or plastic.\\n"
    "  CAN    - any metal or aluminum can.\\n\\n"
    "Reply with ONLY one word: BOTTLE, CAN, or UNKNOWN."
)


def verify_image(
    api_key: str,
    image_path: str,
    model: str,
) -> Tuple[str, Optional[float], str]:
    """
    Returns tuple: (label, confidence, raw_text).
    Confidence is None because this Gemini prompt is categorical.
    """
    client = genai.Client(api_key=api_key)
    image = PIL.Image.open(image_path)

    response = client.models.generate_content(
        model=model,
        contents=[PROMPT, image],
    )

    raw_text = (response.text or "").strip().upper()

    if "BOTTLE" in raw_text:
        return "BOTTLE", None, raw_text
    if "CAN" in raw_text:
        return "CAN", None, raw_text
    return "UNKNOWN", None, raw_text
