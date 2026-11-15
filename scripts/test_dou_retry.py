import unittest
import requests
from scripts.monitor_dou_public_search import build_session
from scripts.monitor_atos_regulatorios import configure_session


class DouRetryTests(unittest.TestCase):
    def test_retry_policy_covers_gateway_and_rate_limit_errors(self):
        for session in (build_session(), configure_session(requests.Session())):
            retry = session.get_adapter("https://").max_retries
            self.assertGreaterEqual(retry.total, 1)
            self.assertIn(429, retry.status_forcelist)
            self.assertIn(502, retry.status_forcelist)
            self.assertIn("GET", retry.allowed_methods)
            self.assertNotIn("POST", retry.allowed_methods)


if __name__ == "__main__":
    unittest.main()
