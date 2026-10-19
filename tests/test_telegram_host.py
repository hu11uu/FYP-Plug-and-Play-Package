"""Tests for the Telegram adapter, with no bot token or network.

The routing functions are called directly with stand-in chat ids, running
the real algorithm on the offline model. Only build_application(), which
needs a live token, is not covered.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import deployments  # noqa: E402
from hosts import telegram_host  # noqa: E402


class TelegramRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = deployments.build(
            "software_engineering", mode="offline",
            database=str(Path(self.tmp.name) / "telegram_feedback.sqlite"),
        )
        self.sessions = {}
        self.chat_id = 424242  # stand-in for a Telegram chat id; no real chat involved

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_creates_a_session_and_greets(self):
        reply = telegram_host.handle_start(self.bot, self.sessions, self.chat_id)
        self.assertIn(self.chat_id, self.sessions)
        self.assertIn("New session started", reply)
        self.assertIn(self.bot.conversation_title, reply)

    def test_first_message_without_start_still_creates_a_session(self):
        # Telegram users often type before /start; the adapter must not crash.
        self.assertNotIn(self.chat_id, self.sessions)
        reply = telegram_host.handle_message(self.bot, self.sessions, self.chat_id, "What are the levels of software testing?")
        self.assertIn(self.chat_id, self.sessions)
        self.assertIn("unit testing", reply.lower())

    def test_empty_message_gets_a_prompt_not_an_error(self):
        reply = telegram_host.handle_message(self.bot, self.sessions, self.chat_id, "   ")
        self.assertEqual(reply, "Send me a question to get started.")

    def test_survey_flow_end_to_end_through_the_pure_router(self):
        telegram_host.handle_message(self.bot, self.sessions, self.chat_id, "Explain the waterfall model")
        reply = telegram_host.handle_message(self.bot, self.sessions, self.chat_id, "Thanks, that helped")
        token = self.sessions[self.chat_id]
        state = self.bot.sessions[token]
        if not state.survey_questions:
            self.skipTest("offline fixture did not trigger a survey on this turn")
        self.assertIn("Reply with", reply)
        self.assertIn("1-5", reply)

        # Wrong number of ratings: adapter should ask again, not crash or silently accept.
        n = len(state.survey_questions)
        bad_reply = telegram_host.handle_message(self.bot, self.sessions, self.chat_id, "4")
        if n != 1:
            self.assertIn(f"exactly {n} numbers", bad_reply)

        good_ratings = ",".join(["4"] * n)
        save_reply = telegram_host.handle_message(self.bot, self.sessions, self.chat_id, good_ratings)
        self.assertEqual(save_reply, "Thanks -- feedback saved.")

    def test_two_chat_ids_get_independent_sessions(self):
        telegram_host.handle_message(self.bot, self.sessions, 111, "What are the levels of software testing?")
        telegram_host.handle_message(self.bot, self.sessions, 222, "Explain the waterfall model")
        self.assertNotEqual(self.sessions[111], self.sessions[222])

    def test_build_application_importable_without_a_token(self):
        # Loading the module and finding build_application() must not need
        # a token or the telegram library.
        self.assertTrue(callable(telegram_host.build_application))


class AzureEnvLoadingRegressionTests(unittest.TestCase):
    """Checks that --model azure reads azure.env before checking the Azure settings.

    This was a real bug. The test runs the script as a subprocess and stops
    before any Telegram network call.
    """

    def test_model_azure_actually_loads_azure_env_file(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            azure_env = Path(tmp) / "azure.env"
            azure_env.write_text(
                "FYP_AZURE_ENDPOINT=https://fake-resource.openai.azure.com\n"
                "FYP_AZURE_DEPLOYMENT=fake-deployment\n"
                "FYP_AZURE_API_VERSION=2024-08-01-preview\n"
                "FYP_AZURE_API_KEY=fake-key-not-real\n"
            )
            telegram_env = Path(tmp) / "telegram.env"
            telegram_env.write_text("TELEGRAM_BOT_TOKEN=123456789:AAFakeTokenNotReal000000000000000\n")

            result = subprocess.run(
                [sys.executable, str(ROOT / "hosts" / "telegram_host.py"),
                 "--deployment", "software_engineering", "--model", "azure",
                 "--env-file", str(telegram_env), "--azure-env-file", str(azure_env)],
                capture_output=True, text=True, timeout=8,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn(
                "Set all four FYP_AZURE_* variables", combined,
                "azure.env was not loaded before AzureProvider() checked it.",
            )
            self.assertIn(
                "Plug-and-play Telegram host", combined,
                "should reach the startup print once credentials validate, before any real network call",
            )


if __name__ == "__main__":
    unittest.main()
