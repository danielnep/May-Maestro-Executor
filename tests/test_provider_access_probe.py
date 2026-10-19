import io
import json
import unittest
from urllib.error import HTTPError

from horizon_core.provider_access_probe import check_deepseek

class Response(io.BytesIO):
    status = 200
    def __init__(self, doc):
        super().__init__(json.dumps(doc).encode())

class ProbeTests(unittest.TestCase):
    def test_missing_key_sends_no_requests(self):
        def fail(*args, **kwargs): raise AssertionError("should not connect")
        self.assertEqual(check_deepseek({}, fail)["status"],"missing_key")

    def test_auth_and_available_without_reporting_balance_values(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            self.assertEqual(request.method,"GET")
            self.assertEqual(request.full_url,"https://api.deepseek.com/user/balance")
            self.assertEqual(request.get_header("Authorization"),"Bearer synthetic-secret")
            return Response({"is_available":True,"balance_infos":[{"total_balance":"1234.56"}]})
        report=check_deepseek({"DEEPSEEK_API_KEY":"synthetic-secret"},opener)
        self.assertTrue(report["usable"])
        self.assertEqual(report["status"],"authenticated")
        self.assertNotIn("1234",str(report))
        self.assertNotIn("synthetic-secret",str(report))
        self.assertEqual(len(seen),1)

    def test_valid_key_insufficient_balance_is_not_misreported_as_usable(self):
        report=check_deepseek({"DEEPSEEK_API_KEY":"x"},lambda *a,**k:Response({"is_available":False}))
        self.assertEqual(report["status"],"authenticated")
        self.assertFalse(report["usable"])

    def test_authorization_and_insufficient_balance_statuses(self):
        for code,expected in [(401,"invalid_credentials"),(403,"invalid_credentials"),(402,"insufficient_balance"),(429,"rate_limited"),(503,"http_error")]:
            with self.subTest(code=code):
                def bad(request,timeout):
                    raise HTTPError(request.full_url,code,"secret diagnostic",{},None)
                report=check_deepseek({"DEEPSEEK_API_KEY":"secret"},bad)
                self.assertEqual(report["status"],expected)
                self.assertNotIn("secret",str(report))
if __name__=="__main__":
    unittest.main()
