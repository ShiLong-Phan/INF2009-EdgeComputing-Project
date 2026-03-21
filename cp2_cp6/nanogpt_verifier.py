import base64
from typing import Optional, Tuple
import requests

# Nanogpt usually follows the OpenAI/OpenRouter standard
NANOGPT_ENDPOINT = "https://nano-gpt.com/api/v1/chat/completions"

PROMPT = (
    "Look at this image carefully.\n"
    "Is the main object in the image a BOTTLE or a CAN?\n\n"
    "Definitions:\n"
    "  BOTTLE - any bottle made of glass or plastic.\n"
    "  CAN    - any metal or aluminum can.\n\n"
    "Reply with ONLY one word: BOTTLE, CAN, or UNKNOWN."
)

def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def verify_image(
    api_key: str,
    image_path: str,
    model: str = "qwen3.5-27b", # Defaulting as requested
) -> Tuple[str, Optional[float], str]:
    """
    Returns tuple: (label, confidence, raw_text).
    Uses NanoGPT API with Vision capabilities.
    """
    base64_image = encode_image(image_path)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 10
    }

    try:
        response = requests.post(NANOGPT_ENDPOINT, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        raw_text = data['choices'][0]['message']['content'].strip().upper()

        if "BOTTLE" in raw_text:
            return "BOTTLE", None, raw_text
        if "CAN" in raw_text:
            return "CAN", None, raw_text
        return "UNKNOWN", None, raw_text
        
    except Exception as e:
        return str(e), None, str(e)
