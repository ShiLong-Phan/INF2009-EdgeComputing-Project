import base64
import requests
import os
import json

# Correct URL from user correction
NANOGPT_ENDPOINT = "https://nano-gpt.com/api/v1/chat/completions"

def test_nanogpt_vision(api_key: str, image_path: str):
    if not os.path.exists(image_path):
        print(f"Error: Image {image_path} not found.")
        return

    with open(image_path, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode('utf-8')

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Model name might need to be 'qwen-3.5-27b' or similar
    payload = {
        "model": "qwen3.5-27b",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Is this a BOTTLE or a CAN or UNKNOWN? Answer with only one word."},
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

    print(f"Sending request to {NANOGPT_ENDPOINT}...")
    try:
        response = requests.post(NANOGPT_ENDPOINT, headers=headers, json=payload, timeout=30)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print("Full Response Body:")
            print(response.text)
            return

        data = response.json()
        print("Success! Response:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    key = os.getenv("NANOGPT_API_KEY", "YOUR_KEY_HERE")
    # Change this to an actual image path you have locally
    img = "data/images/test.jpg" 
    
    if key == "YOUR_KEY_HERE":
        print("Please set your NANOGPT_API_KEY environment variable.")
    else:
        test_nanogpt_vision(key, img)
