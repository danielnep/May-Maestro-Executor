import base64
import io
import json
import unittest
import urllib.error
from horizon_core.gateway import GeminiGateway, ModelGateway, GatewayError
from horizon_core.registry import load_agent


class VisionBoundaryTests(unittest.TestCase):
    def test_actual_image_bytes_are_sent_and_thoughts_excluded(self):
        image = b'\x89PNG\r\n\x1a\nsynthetic-image-boundary'
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[
                {'thought':True,'text':'private reasoning'},
                {'text':json.dumps({'action':'finish','args':{},'summary':'Visible labels inspected'})}]}}]}).encode())
        gateway = GeminiGateway({'GEMINI_API_KEY':'synthetic-key','GEMINI_MODEL':'configured-vision-model',
            'GEMINI_FREE_TIER_CONFIRMED':'true'}, opener)
        result = gateway.inspect_image(load_agent('horizon-ui-guardian'),image,'Check overflow')
        sent = json.loads(requests[0].data)['contents'][0]['parts'][1]['inlineData']
        self.assertEqual(base64.b64decode(sent['data']),image)
        self.assertEqual(sent['mimeType'],'image/png')
        self.assertTrue(result['actual_image_provided'])
        self.assertNotIn('private reasoning',json.dumps(result))
        self.assertNotIn('synthetic-key',requests[0].full_url)

    def test_vision_retries_same_png_on_free_lite_after_quota_only(self):
        image = b'\x89PNG\r\n\x1a\nfixture'
        requests = []
        def opener(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, None)
            return io.BytesIO(json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[
                {'text':json.dumps({'action':'finish','args':{},'summary':'Visual inspection completed'})}
            ]}}]}).encode())
        env = {'GEMINI_API_KEY':'fixture', 'GEMINI_MODEL':'gemini-3.6-flash',
               'GEMINI_FREE_FALLBACK_MODEL':'gemini-3.5-flash-lite',
               'GEMINI_FREE_TIER_CONFIRMED':'true'}
        result = ModelGateway(environ=env, opener=opener).inspect_image(
            load_agent('horizon-ui-guardian'), image, 'Check clipping')
        self.assertEqual(len(requests), 2)
        self.assertIn('/gemini-3.6-flash:',requests[0].full_url)
        self.assertIn('/gemini-3.5-flash-lite:',requests[1].full_url)
        for req in requests:
            self.assertEqual(base64.b64decode(json.loads(req.data)['contents'][0]['parts'][1]['inlineData']['data']),image)
        self.assertTrue(result['actual_image_provided'])
        self.assertEqual(result['metadata']['model'],'gemini-3.5-flash-lite')
        self.assertTrue(result['metadata']['free_tier_model'])
        self.assertEqual(result['metadata']['fallback_reason']['status'],429)

    def test_vision_does_not_fallback_on_auth_error(self):
        calls=[]
        def opener(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)
        env={'GEMINI_API_KEY':'fixture','GEMINI_MODEL':'gemini-3.6-flash',
             'GEMINI_FREE_TIER_CONFIRMED':'true',
             'GEMINI_FREE_FALLBACK_MODEL':'gemini-3.5-flash-lite'}
        with self.assertRaises(GatewayError) as error:
            ModelGateway(environ=env,opener=opener).inspect_image(
                load_agent('qa-engineer'), b'\x89PNG\r\n\x1a\nfixture', 'Inspect text')
        self.assertEqual(error.exception.status,401)
        self.assertEqual(len(calls),1)

    def test_vision_double_quota_exhaustion_preserves_safe_failure_codes(self):
        calls=[]
        def opener(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, None)
        env={'GEMINI_API_KEY':'fixture','GEMINI_MODEL':'gemini-3.6-flash',
             'GEMINI_FREE_TIER_CONFIRMED':'true',
             'GEMINI_FREE_FALLBACK_MODEL':'gemini-3.5-flash-lite'}
        with self.assertRaises(GatewayError) as error:
            ModelGateway(environ=env,opener=opener).inspect_image(
                load_agent('qa-engineer'), b'\x89PNG\r\n\x1a\nfixture', 'Inspect labels')
        self.assertEqual(error.exception.reason,'vision_unavailable')
        self.assertEqual([x['status'] for x in error.exception.failures],[429,429])
        self.assertEqual(len(calls),2)

    def test_invalid_image_rejected_before_request(self):
        calls=[]
        gateway=GeminiGateway({'GEMINI_API_KEY':'x','GEMINI_MODEL':'x','GEMINI_FREE_TIER_CONFIRMED':'true'},lambda *a,**k:calls.append(a))
        with self.assertRaises(GatewayError):
            gateway.inspect_image(load_agent('horizon-ui-guardian'),b'not a screenshot','Inspect')
        self.assertEqual(calls,[])
