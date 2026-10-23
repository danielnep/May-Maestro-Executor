import io
import json
import unittest
import urllib.error
from horizon_core.gateway import GeminiGateway, ModelGateway

AGENT = {'id':'backend-engineer','instructions':'Return finish.', 'tools':['finish']}
MESSAGES = [{'role':'user','content':'Connectivity test: return finish.'}]
ENV = {'GEMINI_API_KEY':'fake','GEMINI_MODEL':'test-model',
       'GEMINI_REQUESTS_ENABLED':'true','GROQ_API_KEY':'fake'}

def reply(provider):
    action = json.dumps({'action':'finish','args':{},'summary':'ok'})
    value = ({'candidates':[{'content':{'parts':[{'text':action}]}}]} if provider == 'gemini'
             else {'choices':[{'finish_reason':'stop','message':{'content':action}}]})
    return io.BytesIO(json.dumps(value).encode())

class AgentRuntimeTests(unittest.TestCase):
    def test_explicit_enable_does_not_require_claim_about_billing_tier(self):
        gateway = GeminiGateway(ENV, lambda *a, **k: reply('gemini'))
        self.assertEqual(gateway.complete(AGENT, MESSAGES)['metadata']['provider'], 'gemini')

    def test_http_errors_preserve_context_across_gemma_and_groq(self):
        for failure in (400,401,403,404,429,500,503,'timeout'):
            with self.subTest(failure=failure):
                calls=[]
                def opener(req, timeout):
                    calls.append((req,timeout))
                    if 'googleapis.com' in req.full_url:
                        if failure == 'timeout': raise TimeoutError()
                        raise urllib.error.HTTPError(req.full_url,failure,'private',{},None)
                    return reply('groq')
                result=ModelGateway(environ=ENV,opener=opener).complete(AGENT,MESSAGES)
                self.assertEqual(result['metadata']['provider'],'groq')
                self.assertEqual(len(calls),3)
                self.assertLessEqual(sum(t for _,t in calls),65)
                self.assertIn('gemma-4-31b-it',calls[1][0].full_url)
                self.assertEqual(json.loads(calls[2][0].data)['messages'][-1], MESSAGES[0])
                self.assertEqual(result['metadata']['fallback_reasons'][0]['status'],None if failure=='timeout' else failure)

class ProbeTests(unittest.TestCase):
    def test_live_probe_is_explicit_about_simulated_failure_and_real_groq_request(self):
        from horizon_core.runtime_provider_probe import check
        calls=[]
        def opener(req, timeout):
            calls.append(req.full_url)
            return reply('gemini' if 'googleapis.com' in req.full_url else 'groq')
        result=check(ENV,opener)
        self.assertEqual([r['provider'] for r in result],['gemini','groq'])
        self.assertEqual(len(calls),2)
        self.assertEqual(result[1]['route'],'forced_fallback')
        self.assertEqual(result[1]['failures'][0]['status'],503)
