"""Bounded synthetic inference checks, without queue writes or customer data."""
import json
import os
import urllib.error
import urllib.request
from .gateway import GatewayError, ModelGateway


def check(environ=None, opener=None):
    env = dict(os.environ if environ is None else environ)
    # The probe must always fit the host's 90-second startup allowance.
    env.update(GEMINI_HTTP_TIMEOUT_SECONDS='15', GROQ_HTTP_TIMEOUT_SECONDS='15',
               GROQ_MAX_OUTPUT_TOKENS='512', GEMINI_MAX_OUTPUT_TOKENS='256')
    agent = {'id':'backend-engineer', 'instructions':'Connectivity check. Return finish.',
             'tools':['finish']}
    messages = [{'role':'user','content':'Return exactly {"action":"finish","args":{},"summary":"ok"}'}]
    reports = []
    for route in ('primary', 'forced_fallback'):
        def request(req, timeout):
            if route == 'forced_fallback' and 'generativelanguage.googleapis.com' in req.full_url:
                raise urllib.error.HTTPError(req.full_url,503,'synthetic probe failure',{},None)
            return (opener or urllib.request.urlopen)(req, timeout=timeout)
        try:
            result = ModelGateway(environ=env, opener=request).complete(agent, messages)
            meta = result['metadata']
            reports.append({'route':route, 'ok':True, 'provider':meta['provider'],
                            'failures':meta.get('fallback_reasons',[])})
        except GatewayError as error:
            reports.append({'route':route, 'ok':False, 'provider':error.provider,
                            'failures':error.failures or [error.as_dict()]})
    return reports


def main():
    for report in check():
        print(json.dumps({'provider_probe':report}), flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
