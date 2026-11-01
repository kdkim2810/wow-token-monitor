import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import monitor as m


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.directory.name)
        m.save_state({'previous_token_price': 310000, 'last_alert_time': 0})

    def tearDown(self):
        os.chdir(self.cwd)
        self.directory.cleanup()

    def test_copper_converts_to_gold(self):
        with patch.object(m, 'request_json', return_value={'price': 3102690000}):
            self.assertEqual(m.get_wow_token_price('token'), 310269)

    def test_missing_zero_or_invalid_price_is_rejected(self):
        for price in [None, 0, -1, True, '2300000000', 2300000000.0]:
            with patch.object(m, 'request_json', return_value={'price': price}):
                with self.assertRaises(m.MonitorError):
                    m.get_wow_token_price('token')

    def test_oauth_token_required(self):
        with patch.dict(os.environ, {'CLIENT_ID': 'id', 'CLIENT_SECRET': 'secret'}), patch.object(m, 'request_json', return_value={}):
            with self.assertRaises(m.MonitorError):
                m.get_access_token()

    def test_discord_rejection_fails(self):
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://example.invalid'}), patch.object(m.SESSION, 'request', return_value=Mock(status_code=403)):
            with self.assertRaisesRegex(m.MonitorError, 'HTTP 403'):
                m.send_discord('test')

    def test_telegram_ok_false_fails(self):
        with patch.dict(os.environ, {'TELEGRAM_TOKEN': 'secret', 'TELEGRAM_CHAT_ID': 'id'}), patch.object(m, 'request_json', return_value={'ok': False}):
            with self.assertRaises(m.MonitorError):
                m.send_telegram('test')

    def test_telegram_optional_does_not_claim_delivery(self):
        with patch.dict(os.environ, {'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}):
            self.assertFalse(m.send_telegram('test'))

    def test_dry_run_does_not_notify_or_write(self):
        before = Path(m.STATE_FILE).read_bytes()
        with patch.object(m, 'get_access_token', return_value='token'), patch.object(m, 'get_wow_token_price', return_value=230000), patch.object(m, 'send_discord') as discord, patch.object(m, 'send_telegram') as telegram:
            self.assertEqual(m.main(dry_run=True), 0)
            discord.assert_not_called()
            telegram.assert_not_called()
        self.assertEqual(Path(m.STATE_FILE).read_bytes(), before)

    def test_discord_failure_preserves_previous_price(self):
        with patch.object(m, 'get_access_token', return_value='token'), patch.object(m, 'get_wow_token_price', return_value=230000), patch.object(m, 'send_discord', side_effect=m.MonitorError('HTTP 500')), patch.object(m, 'send_telegram') as telegram:
            self.assertEqual(m.main(), 1)
            telegram.assert_not_called()
        self.assertEqual(m.load_state()['previous_token_price'], 310000)

    def test_telegram_failure_does_not_advance_cooldown(self):
        with patch.object(m, 'get_access_token', return_value='token'), patch.object(m, 'get_wow_token_price', return_value=230000), patch.object(m, 'send_discord'), patch.object(m, 'send_telegram', side_effect=m.MonitorError('fail')):
            self.assertEqual(m.main(), 1)
        state = m.load_state()
        self.assertEqual(state['previous_token_price'], 230000)
        self.assertEqual(state['last_alert_time'], 0)

    def test_target_boundary_and_successful_cooldown(self):
        with patch.object(m, 'get_access_token', return_value='token'), patch.object(m, 'get_wow_token_price', return_value=230000), patch.object(m, 'send_discord') as discord, patch.object(m, 'send_telegram', return_value=True) as telegram, patch.object(m.time, 'time', return_value=10000):
            self.assertEqual(m.main(), 0)
            self.assertTrue(discord.call_args.kwargs['mention'])
            self.assertIn('<@1093168060166307840>', discord.call_args.args[0])
            self.assertIn('<@829217044645281813>', discord.call_args.args[0])
            self.assertEqual(m.main(), 0)
            self.assertEqual(telegram.call_count, 1)
        self.assertEqual(m.load_state()['last_alert_time'], 10000)

    def test_above_target_only_regular_report(self):
        with patch.object(m, 'get_access_token', return_value='token'), patch.object(m, 'get_wow_token_price', return_value=230001), patch.object(m, 'send_discord') as discord, patch.object(m, 'send_telegram') as telegram:
            self.assertEqual(m.main(), 0)
            self.assertFalse(discord.call_args.kwargs['mention'])
            telegram.assert_not_called()

    def test_read_api_retries_transient_error(self):
        success = Mock(status_code=200)
        success.json.return_value = {'price': 3102690000}
        with patch.object(m.SESSION, 'request', side_effect=[requests.ConnectionError('secret'), Mock(status_code=503), success]) as request, patch.object(m.time, 'sleep'):
            self.assertEqual(m.get_wow_token_price('token'), 310269)
            self.assertEqual(request.call_count, 3)

    def test_delivery_exception_does_not_leak_token_or_retry_blindly(self):
        with patch.object(m.SESSION, 'request', side_effect=requests.ConnectionError('https://secret-token')) as request:
            with self.assertRaises(m.MonitorError) as caught:
                m.request_json('POST', 'https://secret-token', '알림')
            self.assertNotIn('secret-token', str(caught.exception))
            self.assertEqual(request.call_count, 1)

    def test_rate_limit_retries(self):
        limited = Mock(status_code=429)
        limited.json.return_value = {'retry_after': 1}
        with patch.object(m.SESSION, 'request', side_effect=[limited, Mock(status_code=204)]) as request, patch.object(m.time, 'sleep'):
            self.assertEqual(m.request_json('POST', 'url', '알림'), {})
            self.assertEqual(request.call_count, 2)

    def test_invalid_state_is_not_reset(self):
        Path(m.STATE_FILE).write_text(json.dumps({'previous_token_price': -1, 'last_alert_time': 0}))
        with self.assertRaises(m.MonitorError):
            m.load_state()
        self.assertEqual(json.loads(Path(m.STATE_FILE).read_text())['previous_token_price'], -1)


if __name__ == '__main__':
    unittest.main()
