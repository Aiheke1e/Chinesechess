import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import glm_client


class GLMTransportTests(unittest.TestCase):
    def response(self, code=0, stderr=b"", data=None, status=b"200"):
        body = data or {"choices": [{"message": {"content": "下午好，接着聊。"}}]}
        return SimpleNamespace(returncode=code, stderr=stderr,
            stdout=json.dumps(body).encode() + b"\n" + status)

    def test_exact_windows_offline_revocation_error_retries_once_with_chain_checks_enabled(self):
        failed = self.response(35, b"schannel: CRYPT_E_REVOCATION_OFFLINE (0x80092013)", status=b"000")
        with patch.object(glm_client, "load_api_key", return_value="private-key"), patch.object(
                glm_client.subprocess, "run", side_effect=[failed, self.response()]) as run:
            self.assertEqual(glm_client.chat([]), "下午好，接着聊。")
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("--ssl-revoke-best-effort", run.call_args_list[0].args[0])
        self.assertIn("--ssl-revoke-best-effort", run.call_args_list[1].args[0])
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], ["curl.exe", "--disable"])
            for forbidden in ("--insecure", "-k", "--ssl-no-revoke", "private-key"):
                self.assertNotIn(forbidden, command)

    def test_revoked_invalid_and_other_tls_errors_are_not_retried_or_hidden(self):
        for code, stderr in ((35, b"CRYPT_E_REVOKED"), (60, b"CERT_E_EXPIRED"),
                             (35, b"unknown handshake failure"), (28, b"timeout")):
            with self.subTest(code=code, stderr=stderr), patch.object(
                    glm_client, "load_api_key", return_value="private-key"), patch.object(
                    glm_client.subprocess, "run", return_value=self.response(code, stderr, status=b"000")) as run:
                with self.assertRaises(RuntimeError) as error: glm_client.chat([])
                self.assertIn(f"curl {code}", str(error.exception))
                self.assertNotIn("private-key", str(error.exception))
                self.assertEqual(run.call_count, 1)

    def test_http_errors_and_failed_fallback_do_not_loop(self):
        offline = self.response(35, b"CRYPT_E_REVOCATION_OFFLINE", status=b"000")
        with patch.object(glm_client, "load_api_key", return_value="private-key"), patch.object(
                glm_client.subprocess, "run", return_value=offline) as run:
            with self.assertRaises(RuntimeError): glm_client.chat([])
            self.assertEqual(run.call_count, 2)
        with patch.object(glm_client, "load_api_key", return_value="private-key"), patch.object(
                glm_client.subprocess, "run", return_value=self.response(
                    data={"error": {"code": "invalid_api_key"}}, status=b"401")) as run:
            with self.assertRaisesRegex(RuntimeError, "invalid_api_key"): glm_client.chat([])
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
