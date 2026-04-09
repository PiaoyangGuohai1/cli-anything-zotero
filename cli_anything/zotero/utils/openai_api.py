from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_API_URL = "https://api.openai.com/v1/chat/completions"
USER_AGENT = "cli-anything-zotero/0.4.0"


def _extract_text(response_payload: dict[str, Any]) -> str:
    """Extract text from a Chat Completions API response."""
    # Chat Completions format
    choices = response_payload.get("choices")
    if choices and isinstance(choices, list):
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    # Responses API format (legacy fallback)
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for item in response_payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content_item in item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts).strip()


def create_text_response(
    *,
    api_key: str,
    model: str,
    instructions: str,
    input_text: str,
    timeout: int = 60,
) -> dict[str, Any]:
    api_url = os.environ.get("CLI_ANYTHING_ZOTERO_OPENAI_URL", "").strip() or DEFAULT_API_URL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": input_text},
        ],
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    answer = _extract_text(response_payload)
    if not answer:
        raise RuntimeError("OpenAI API returned no text output")
    return {
        "response_id": response_payload.get("id"),
        "answer": answer,
        "raw": response_payload,
    }
