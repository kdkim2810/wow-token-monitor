import unittest
from unittest.mock import Mock, patch

import monitor


class RetryHeaderTests(unittest.TestCase):
    def test_invalid_json_respects_long_retry_after_header(self):
        limited = Mock(status_code=429, headers={'Retry-After': '120'})
        limited.json.side_effect = ValueError('not JSON')
        with patch.object(monitor.SESSION, 'request', return_value=limited) as request, patch.object(monitor.time, 'sleep') as sleep:
            with self.assertRaises(monitor.MonitorError):
                monitor.request_json('POST', 'url', '알림')
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()
