import os
import unittest
from unittest.mock import patch, MagicMock
from bot import alerts

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,dict(SMTP_HOST='smtp.example.test',SMTP_USERNAME='test',SMTP_PASSWORD='test',ALERT_FROM_EMAIL='bot@example.test',ALERT_TO_EMAIL='support@czrex.com'),clear=True)
        self.env.start()
    def tearDown(self):self.env.stop()
    def test_starttls_before_authentication_and_recipient(self):
        client=MagicMock();client.send_message.return_value={}
        with patch('smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value=client
            alerts.send('Failure test',None)
        names=[c[0] for c in client.method_calls]
        self.assertLess(names.index('starttls'),names.index('login'))
        message=client.send_message.call_args.args[0]
        self.assertEqual(message['To'],'support@czrex.com')
        self.assertNotIn('SMTP_PASSWORD',str(message))
    def test_no_insecure_port(self):
        os.environ['SMTP_PORT']='25'
        with patch('smtplib.SMTP') as smtp, self.assertRaises(ValueError):alerts.send('test',None)
        smtp.assert_not_called()
    def test_missing_credentials_fail_closed(self):
        del os.environ['SMTP_PASSWORD']
        self.assertFalse(alerts.configured())
        with self.assertRaises(ValueError):alerts.send('test',None)
    def test_tls_failure_never_authenticates(self):
        client=MagicMock();client.starttls.side_effect=RuntimeError('TLS failed')
        with patch('smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value=client
            with self.assertRaises(RuntimeError):alerts.send('test',None)
        client.login.assert_not_called()
