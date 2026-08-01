import requests
import base64
import io
import json
import subprocess
import time


OLLAMA_VISION_MODEL = "qwen3-vl:8b-instruct"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def ensure_ollama_running():
    """Pings the Ollama local server, starts it if it's dead."""
    try:
        requests.get(f"{OLLAMA_BASE_URL}/api/version", timeout=2)
        return True
    except:
        print("Starting Ollama...")
        try:
            # creationflags=0x08000000 prevents a command prompt window from popping up
            subprocess.Popen(["ollama", "serve"], creationflags=0x08000000)
            time.sleep(4)
            return True
        except:
            return False


def query_ollama_vision(pil_image, prompt):
    """Base64 encodes an image and queries the configured Ollama vision model."""
    buffered = io.BytesIO()
    pil_image.save(buffered, format="PNG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    payload = {
        "model": OLLAMA_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [img_b64],
        }],
        "format": "json",  # STRICT JSON FORCING
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0}
    }

    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=45)
        data = response.json()
        if "error" in data:
            return json.dumps({"error": data['error']})
        return data.get("message", {}).get("content", "{}").strip()
    except Exception as e:
        return json.dumps({"error": str(e)})
