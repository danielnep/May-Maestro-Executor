"""Read Gemini's model catalog with the saved key; no inference or key logging.

Catalog presence does not prove quota, free-tier billing or generation success.
Official API: https://ai.google.dev/api/models#method:-models.list
"""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request


def discover(environ=None, opener=None):
    env = os.environ if environ is None else environ
    opener = opener or urllib.request.urlopen
    key = env.get('GEMINI_API_KEY')
    if not key:
        return {'status': 'missing_key', 'models': [], 'inference_requests': 0}
    models, token, seen = [], '', set()
    while True:
        query = urllib.parse.urlencode({'pageSize': 1000, **({'pageToken': token} if token else {})})
        request = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models?' + query,
                                         headers={'x-goog-api-key': key})
        try:
            with opener(request, timeout=45) as response:
                page = json.loads(response.read())
        except urllib.error.HTTPError as error:
            return {'status': 'http_error', 'http_status': error.code, 'models': [], 'inference_requests': 0}
        except (OSError, ValueError, urllib.error.URLError):
            return {'status': 'catalog_unavailable', 'models': [], 'inference_requests': 0}
        if not isinstance(page, dict) or not isinstance(page.get('models', []), list):
            return {'status': 'invalid_catalog', 'models': [], 'inference_requests': 0}
        for model in page.get('models', []):
            name = model.get('name', '') if isinstance(model, dict) else ''
            if re.fullmatch(r'models/[a-zA-Z0-9._-]{1,120}', name) and 'generateContent' in model.get('supportedGenerationMethods', []):
                models.append(name.removeprefix('models/'))
        token = page.get('nextPageToken', '')
        if not token:
            break
        if not isinstance(token, str) or token in seen:
            return {'status': 'invalid_pagination', 'models': sorted(set(models)), 'inference_requests': 0}
        seen.add(token)
    configured = env.get('GEMINI_MODEL') or 'gemini-3.8-flash'
    return {'status': 'ok', 'models': sorted(set(models)), 'configured_model': configured,
            'configured_model_listed': configured in models, 'billing_verified': False,
            'quota_verified': False, 'inference_requests': 0}


if __name__ == '__main__':
    result = discover()
    Path('runtime').mkdir(exist_ok=True)
    Path('runtime/gemini-catalog.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
