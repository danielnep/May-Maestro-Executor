"""Safe, read-only API credential probe: no completion, no balance amounts or keys."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BALANCE_URL = "https://api.deepseek.com/user/balance"


def check_deepseek(environ=None, opener=None):
    env = os.environ if environ is None else environ
    key = env.get("DEEPSEEK_API_KEY")
    if not key:
        return {"provider": "deepseek", "status": "missing_key", "usable": False, "requests_sent": 0}
    request = Request(BALANCE_URL, method="GET", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
        "User-Agent": "Horizon-Agent-ReadOnly-Credential-Probe/1"})
    try:
        with (opener or urlopen)(request, timeout=20) as response:
            if response.status != 200:
                return {"provider": "deepseek", "status": "unexpected_http",
                        "http_status": response.status, "usable": False, "requests_sent": 1}
            value = json.loads(response.read(16000))
        if not isinstance(value, dict) or type(value.get("is_available")) is not bool:
            return {"provider": "deepseek", "status": "invalid_response", "usable": False, "requests_sent": 1}
        return {"provider": "deepseek", "status": "authenticated",
                "usable": value["is_available"], "requests_sent": 1,
                "inference_requests": 0}
    except HTTPError as error:
        code = error.code
        return {"provider": "deepseek", "status": (
            "invalid_credentials" if code in (401, 403) else
            "insufficient_balance" if code == 402 else
            "rate_limited" if code == 429 else "http_error"),
            "http_status": code, "usable": False, "requests_sent": 1, "inference_requests": 0}
    except (URLError, TimeoutError, OSError, ValueError):
        return {"provider": "deepseek", "status": "unavailable", "usable": False,
                "requests_sent": 1, "inference_requests": 0}


def main():
    report = check_deepseek()
    print(json.dumps(report, sort_keys=True))
    if report.get("status") != "authenticated" or not report.get("usable"):
        print("::warning::DeepSeek credential or available balance not confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
