import base64
import io
import json
import unittest
from horizon_core.gateway import GeminiGateway, GatewayError
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

    def test_invalid_image_rejected_before_request(self):
        calls=[]
        gateway=GeminiGateway({'GEMINI_API_KEY':'x','GEMINI_MODEL':'x','GEMINI_FREE_TIER_CONFIRMED':'true'},lambda *a,**k:calls.append(a))
        with self.assertRaises(GatewayError):
            gateway.inspect_image(load_agent('horizon-ui-guardian'),b'not a screenshot','Inspect')
        self.assertEqual(calls,[])
