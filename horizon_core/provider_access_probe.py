"""Read-only Groq model-access probe; no inference, tokens or credentials disclosed."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS_URL = "https://api.groq.com/openai/v1/models"
UI_MODEL = "qwen/qwen3.8-27b"
CODE_MODEL = "llama-3.3-70b-versatile"


def check_groq(environ=None, opener=None):
    env = os.environ if environ is None else environ
    key = env.get("GROQ_API_KEY") or env.get("API_IA_GROQ")
    if not key:
        return {"provider": "groq", "status": "missing_key", "usable": False, "requests_sent": 0}
    req = Request(MODELS_URL, method="GET", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
        "User-Agent": "Horizon-Agent-ReadOnly-Groq-Model-Probe/1"})
    try:
        with (opener or urlopen)(req, timeout=20) as response:
            if response.status != 200:
                return {"provider": "groq", "status": "unexpected_http", "usable": False,
                        "http_status": response.status, "requests_sent": 1}
            value = json.loads(response.read(100000))
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            return {"provider": "groq", "status": "invalid_response",
                    "usable": False, "requests_sent": 1}
        ids = {item.get("id") for item in value["data"] if isinstance(item, dict)}
        models = [env.get("GROQ_UI_MODEL") or UI_MODEL, env.get("GROQ_CODE_MODEL") or CODE_MODEL]
        return {"provider": "groq", "status": "authenticated",
                "usable": any(model in ids for model in models), "requests_sent": 1,
                "models_listed": {model: model in ids for model in models},
                "inference_requests": 0}
    except HTTPError as error:
        status = ("invalid_credentials" if error.code in (401, 403) else
                  "rate_limited" if error.code == 429 else "http_error")
        return {"provider": "groq", "status": status, "usable": False,
                "http_status": error.code, "requests_sent": 1, "inference_requests": 0}
    except (URLError, TimeoutError, OSError, ValueError):
        return {"provider": "groq", "status": "unavailable", "usable": False,
                "requests_sent": 1, "inference_requests": 0}


def main():
    report = check_groq()
    print(json.dumps(report, sort_keys=True))
    if report.get("status") != "authenticated" or not report.get("usable"):
        print("::warning::Groq key or access to configured models not confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
