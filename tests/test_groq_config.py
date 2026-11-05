"""Offline credential configuration tests; every token below is a synthetic placeholder."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oem_agent.groq_config import GroqConfigError, MODEL, load_local_env
from oem_agent.groq_hosted import GroqClient, GroqError


class GroqConfigurationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / ".env"
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_missing_file_uses_process_environment_without_network(self):
        with patch("http.client.HTTPSConnection") as connection:
            result = load_local_env(self.path)
        self.assertEqual(result, {"dotenv_present": False, "api_key_available": False, "model_supported": True})
        connection.assert_not_called()
        self.assertNotIn("GROQ_API_KEY", os.environ)

    def test_loads_two_names_and_returns_only_booleans(self):
        self.write(f"GROQ_API_KEY=OFFLINE_TEST_TOKEN\nGROQ_MODEL={MODEL}\n")
        result = load_local_env(self.path)
        self.assertTrue(all(type(value) is bool for value in result.values()))
        self.assertTrue(result["api_key_available"])
        self.assertEqual(os.environ["GROQ_API_KEY"], "OFFLINE_TEST_TOKEN")
        self.assertEqual(os.environ["GROQ_MODEL"], MODEL)
        self.assertNotIn("OFFLINE_TEST_TOKEN", json.dumps(result))

    def test_process_environment_including_empty_values_wins(self):
        self.write(f"GROQ_API_KEY=FILE_TEST_TOKEN\nGROQ_MODEL={MODEL}\n")
        os.environ["GROQ_API_KEY"] = "PROCESS_TEST_TOKEN"
        load_local_env(self.path)
        self.assertEqual(os.environ["GROQ_API_KEY"], "PROCESS_TEST_TOKEN")
        os.environ["GROQ_API_KEY"] = ""
        self.assertFalse(load_local_env(self.path)["api_key_available"])
        self.assertEqual(os.environ["GROQ_API_KEY"], "")

    def test_default_location_is_explicit_project_file(self):
        self.write("GROQ_API_KEY=OFFLINE_TEST_TOKEN\n")
        with patch("oem_agent.groq_config.ENV_PATH", self.path):
            self.assertTrue(load_local_env()["api_key_available"])

    def test_bom_comments_blank_lines_and_quoted_values(self):
        self.write(f'\ufeff# comment\n\n GROQ_API_KEY = "OFFLINE_TEST_TOKEN"\nGROQ_MODEL=\'{MODEL}\'\n')
        self.assertTrue(load_local_env(self.path)["api_key_available"])
        self.assertEqual(os.environ["GROQ_MODEL"], MODEL)

    def test_unrelated_credentials_and_routing_names_are_not_exported(self):
        self.write("OPENAI_API_KEY=OTHER_TEST_TOKEN\nHTTPS_PROXY=http://example.invalid\nGROQ_BASE_URL=https://example.invalid\nGROQ_API_KEY=OFFLINE_TEST_TOKEN\n")
        load_local_env(self.path)
        self.assertEqual(set(os.environ), {"GROQ_API_KEY"})

    def test_empty_key_does_not_claim_readiness(self):
        self.write(f"GROQ_API_KEY=\nGROQ_MODEL={MODEL}\n")
        self.assertFalse(load_local_env(self.path)["api_key_available"])

    def test_malformed_duplicate_interpolation_or_invalid_key_is_atomic_and_redacted(self):
        for bad_line in ("GROQ_API_KEY=OTHER_TEST_TOKEN", "no assignment OFFLINE_TEST_TOKEN", "export GROQ_MODEL=OFFLINE_TEST_TOKEN",
                         "GROQ_MODEL=${OFFLINE_TEST_TOKEN}", "GROQ_MODEL=`OFFLINE_TEST_TOKEN`", "GROQ_MODEL=\'OFFLINE_TEST_TOKEN", "GROQ_MODEL=paid/other"):
            with self.subTest(case=bad_line.split('=')[0]):
                self.write("GROQ_API_KEY=OFFLINE_TEST_TOKEN\n"+bad_line+"\n")
                with self.assertRaises(GroqConfigError) as error:
                    load_local_env(self.path)
                self.assertNotIn("OFFLINE_TEST_TOKEN", str(error.exception))
                self.assertNotIn("GROQ_API_KEY", os.environ)
        self.write("GROQ_API_KEY=OFFLINE TEST TOKEN\n")
        with self.assertRaises(GroqConfigError):
            load_local_env(self.path)
        self.assertNotIn("GROQ_API_KEY", os.environ)

    def test_file_size_encoding_and_read_errors_are_sanitized(self):
        for content in (b"x"*16385, b"\xff"):
            self.path.write_bytes(content)
            with self.assertRaises(GroqConfigError) as error:
                load_local_env(self.path)
            self.assertNotIn(str(self.path), str(error.exception))
        with patch.object(Path, "open", side_effect=PermissionError("PRIVATE_OS_MESSAGE")):
            with self.assertRaises(GroqConfigError) as error:
                load_local_env(self.path)
        self.assertEqual(error.exception.code, "local_env_unavailable")
        self.assertNotIn("PRIVATE_OS_MESSAGE", str(error.exception))

    def test_existing_file_is_never_changed(self):
        self.write("GROQ_API_KEY=OFFLINE_TEST_TOKEN\n")
        before = self.path.read_bytes()
        load_local_env(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_process_model_override_cannot_select_other_models(self):
        self.write("GROQ_API_KEY=OFFLINE_TEST_TOKEN\n")
        for value in ("paid/other", "", "PRIVATE_MODEL_VALUE"):
            os.environ["GROQ_MODEL"] = value
            with self.assertRaises(GroqConfigError) as error:
                load_local_env(self.path)
            self.assertEqual(error.exception.code, "groq_model_not_allowed")
            self.assertNotIn(value or "NONEXISTENT", str(error.exception))
            self.assertNotIn("GROQ_API_KEY", os.environ)

    def test_direct_client_rejects_wrong_model_before_connecting(self):
        os.environ.update(GROQ_MODEL="PRIVATE_MODEL_VALUE", GROQ_API_KEY="OFFLINE_TEST_TOKEN")
        with patch("oem_agent.groq_hosted.http.client.HTTPSConnection") as connection:
            with self.assertRaises(GroqError) as error:
                GroqClient().availability(9999999999)
        self.assertEqual(error.exception.code, "groq_model_not_allowed")
        self.assertNotIn("PRIVATE_MODEL_VALUE", str(error.exception))
        connection.assert_not_called()

    def test_offline_cli_checks_available_key_without_network_or_secret_output(self):
        from examples.stage5_3_groq_smoke import main
        self.write(f"GROQ_API_KEY=OFFLINE_TEST_TOKEN\nGROQ_MODEL={MODEL}\n")
        with patch("oem_agent.groq_config.ENV_PATH", self.path), patch("http.client.HTTPSConnection") as connection, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["--check-config"]), 0)
        self.assertTrue(json.loads(output.getvalue())["api_key_available"])
        self.assertTrue(all(type(v) is bool for v in json.loads(output.getvalue()).values()))
        self.assertNotIn("OFFLINE_TEST_TOKEN", output.getvalue())
        connection.assert_not_called()

    def test_offline_cli_missing_or_invalid_key_is_not_ready(self):
        from examples.stage5_3_groq_smoke import main
        for text in ("GROQ_API_KEY=\n", "GROQ_API_KEY=OFFLINE TEST TOKEN\n"):
            self.write(text)
            with patch("oem_agent.groq_config.ENV_PATH", self.path), patch("http.client.HTTPSConnection") as connection, patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["--check-config"]), 1)
            self.assertNotIn("OFFLINE TEST TOKEN", output.getvalue())
            self.assertTrue(all(type(v) is bool for v in json.loads(output.getvalue()).values()))
            connection.assert_not_called()

    def test_cli_loads_local_key_before_explicit_metadata_check(self):
        from examples.stage5_3_groq_smoke import main
        self.write("GROQ_API_KEY=OFFLINE_TEST_TOKEN\n")
        def availability(deadline):
            self.assertEqual(os.environ.get("GROQ_API_KEY"), "OFFLINE_TEST_TOKEN")
            return {"active": True}
        with patch("oem_agent.groq_config.ENV_PATH", self.path), patch.object(GroqClient, "availability", side_effect=availability), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main([]), 0)
        self.assertNotIn("OFFLINE_TEST_TOKEN", output.getvalue())

    def test_offline_and_transfer_modes_cannot_be_combined(self):
        from examples.stage5_3_groq_smoke import main
        with patch("sys.stderr", new_callable=io.StringIO), patch("http.client.HTTPSConnection") as connection, self.assertRaises(SystemExit):
            main(["--check-config", "--allow-synthetic-transfer"])
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
