"""Authenticated, cached calls to the user's local OpenAI-compatible MLX-VLM server."""

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from urllib import request

MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
ENDPOINT = "http://127.0.0.1:11434/v1/chat/completions"


def load_local_environment(root):
    path = Path(root) / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if (
            separator
            and key.strip() == "MLX_VLM_API_KEY"
            and key.strip() not in os.environ
        ):
            os.environ[key.strip()] = value.strip().strip("\"'")


class LocalModel:
    def __init__(self, cache_dir, endpoint=ENDPOINT, model=MODEL):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint = endpoint
        self.model = model
        self.usage = {
            "calls": 0,
            "cache_hits": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_seconds": 0.0,
        }

    def cache_key(self, prompt, required_keys, image_path):
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                [self.model, prompt, required_keys], ensure_ascii=False, sort_keys=True
            ).encode()
        )
        if image_path:
            digest.update(Path(image_path).read_bytes())
        return digest.hexdigest()

    def extract(self, prompt, required_keys, image_path=None):
        key = self.cache_key(prompt, required_keys, image_path)
        cache_file = self.cache_dir / (key + ".json")
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            self.usage["cache_hits"] += 1
            return data
        token = os.environ.get("MLX_VLM_API_KEY")
        if not token:
            raise RuntimeError(
                "MLX_VLM_API_KEY is required for uncached local model calls"
            )
        content = [{"type": "text", "text": prompt + "\nReturn only a JSON object."}]
        if image_path:
            image = Path(image_path)
            mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(image.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:" + mime + ";base64," + encoded},
                }
            )
        properties = {}
        for name in required_keys:
            if name == "records":
                properties[name] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource_id": {
                                "type": "string",
                                "pattern": "^(account|search|support|analytics|export|data_flow|backup|audit_record|tombstone|marketing|email_delivery|cache)_[0-9]+$",
                            },
                            "state": {"type": "string"},
                        },
                        "required": ["resource_id", "state"],
                    },
                }
            elif name in ("deletion_requested", "ok"):
                properties[name] = {"type": "boolean"}
            else:
                properties[name] = {"type": "string"}
        schema = {"type": "object", "properties": properties, "required": required_keys}
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "audit_extraction", "schema": schema},
            },
        }
        call = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        started = time.monotonic()
        with request.urlopen(call, timeout=600) as response:
            result = json.load(response)
        self.usage["elapsed_seconds"] += time.monotonic() - started
        self.usage["calls"] += 1
        raw = result["choices"][0]["message"]["content"]
        if isinstance(raw, list):
            raw = "".join(
                part.get("text", "") for part in raw if isinstance(part, dict)
            )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
        if not isinstance(data, dict) or any(
            name not in data for name in required_keys
        ):
            raise ValueError("local model response omitted required JSON fields")
        usage = result.get("usage") or {}
        self.usage["input_tokens"] += (
            usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        )
        self.usage["output_tokens"] += (
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        )
        cache_file.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
        return data
