import io
import json
import unittest
import urllib.error
from horizon_core.gateway import GroqGateway

class ProviderHeadersTests(unittest.TestCase):
    def test_groq_request_identifies_executor_and_accepts_json(self):
        seen=[]
        def opener(req, timeout):
            seen.append(req)
            if not req.get_header('User-agent'):
                raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(b'error code: 1010'))
            action=json.dumps({'action':'finish','args':{},'summary':'ok'})
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop',
                'message':{'content':action}}]}).encode())
        agent={'id':'backend-engineer','instructions':'Connectivity test','tools':['finish']}
        result=GroqGateway({'GROQ_API_KEY':'synthetic'},opener).complete(
            agent,[{'role':'user','content':'Return finish'}])
        self.assertEqual(result['metadata']['provider'],'groq')
        self.assertEqual(seen[0].get_header('User-agent'),'Horizon-Agent-Executor/1')
        self.assertEqual(seen[0].get_header('Accept'),'application/json')
