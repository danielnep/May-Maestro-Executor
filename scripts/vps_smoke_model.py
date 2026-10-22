"""Read-only smoke test of localhost CPU model inference. No credentials or task writes."""
import json
import urllib.request


def main():
    body = {
        "model": "qwen3:4b",
        "messages": [{"role": "user", "content": 'Return exactly this JSON object: {"action":"finish","args":{},"summary":"ok"}'}],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 512, "num_predict": 48}
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        headers={"Content-Type": "application/json"},
        data=json.dumps(body).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError("Local model unavailable")
        data = json.load(response)
    result = json.loads(data["message"]["content"])
    if result != {"action": "finish", "args": {}, "summary": "ok"}:
        raise RuntimeError("Local model returned an unexpected action")
    print(json.dumps({"local_model": "qwen3:4b", "result": "valid_json_action",
                      "prompt_tokens": data.get("prompt_eval_count"),
                      "completion_tokens": data.get("eval_count"),
                      "elapsed_s": round(data.get("total_duration", 0) / 1e9, 1)}))


if __name__ == "__main__":
    main()
