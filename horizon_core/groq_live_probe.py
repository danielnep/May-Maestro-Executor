"""Live Groq API smoke test for GitHub Actions; never prints credentials or responses."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def probe(environ=None, opener=None):
    env = os.environ if environ is None else environ
    key = env.get("GROQ_API_KEY") or env.get("API_IA_GROQ")
    model = env.get("GROQ_MODEL") or DEFAULT_MODEL
    if not key:
        return {"provider": "groq", "status": "missing_key", "requests_sent": 0, "usable": False}
    opening = opener or urlopen
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    def send(route, data=None):
        request = Request(BASE + route, headers=headers, method="GET" if data is None else "POST",
                          data=json.dumps(data).encode() if data is not None else None)
        with opening(request, timeout=25) as response:
            return json.load(response)
    try:
        catalog = send("/models")
        models = [x.get("id") for x in catalog.get("data", []) if isinstance(x, dict)]
        if model not in models:
            return {"provider": "groq", "status": "model_unavailable",
                    "requests_sent": 1, "model": model, "usable": False}
        payload = {"model": model, "messages": [
            {"role": "system", "content": "Return ONLY one JSON object with keys action, args and summary. No other text."},
            {"role": "user", "content": 'Return {"action":"finish","args":{},"summary":"ok"}.'}],
            "response_format": {"type": "json_object"}, "max_completion_tokens": 512}
        if model.startswith("openai/gpt-oss-"):
            payload.update(reasoning_effort="low", include_reasoning=False)
        result = send("/chat/completions", payload)
        choice = result["choices"][0]
        output = json.loads(choice["message"]["content"])
        valid = choice.get("finish_reason") == "stop" and isinstance(output, dict) and (
            set(output) == {"action", "args", "summary"} and output["action"] == "finish" and
            isinstance(output["args"], dict) and isinstance(output["summary"], str))
        return {"provider": "groq", "status": "ok" if valid else "invalid_action",
                "requests_sent": 2, "model": model, "usable": bool(valid)}
    except HTTPError as error:
        return {"provider": "groq", "status": "http_error", "http_status": error.code,
                "requests_sent": 1, "model": model, "usable": False}
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError):
        return {"provider": "groq", "status": "unavailable",
                "requests_sent": 1, "model": model, "usable": False}


if __name__ == "__main__":
    result = probe()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["usable"] else 1)
