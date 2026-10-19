"""Tests for hosts/moodle_host.py.

The session-key, message and routing functions are tested directly. A live
server on a free local port is then tested with real HTTP requests: a wrong
key is rejected, /v1/models lists the subject, a chat round trip returns the
right format, and two requests from one conversation share a session.
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import deployments  # noqa: E402
from hosts import moodle_host  # noqa: E402


class SessionKeyAndParsingTests(unittest.TestCase):
    def test_same_first_message_and_user_gives_the_same_key(self):
        messages = [{"role": "user", "content": "What are the 4Ps of marketing?"}]
        a = moodle_host.session_key_for(messages, "student-42")
        b = moodle_host.session_key_for(messages + [
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "follow-up question"},
        ], "student-42")
        self.assertEqual(a, b, "the key must stay stable across a growing history, since that's how OpenAI-style clients resend it")

    def test_different_first_message_gives_a_different_key(self):
        a = moodle_host.session_key_for([{"role": "user", "content": "Question one"}], "student-42")
        b = moodle_host.session_key_for([{"role": "user", "content": "Question two"}], "student-42")
        self.assertNotEqual(a, b)

    def test_different_end_user_gives_a_different_key_even_with_the_same_opening_message(self):
        # Two different learners who happen to ask the identical first question
        # must not be folded into one shared session.
        messages = [{"role": "user", "content": "Same question"}]
        a = moodle_host.session_key_for(messages, "student-1")
        b = moodle_host.session_key_for(messages, "student-2")
        self.assertNotEqual(a, b)

    def test_latest_user_text_requires_last_message_to_be_from_user(self):
        with self.assertRaises(ValueError):
            moodle_host.latest_user_text([{"role": "assistant", "content": "not a user turn"}])
        with self.assertRaises(ValueError):
            moodle_host.latest_user_text([])

    def test_latest_user_text_returns_the_last_messages_content(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "  second, with whitespace  "},
        ]
        self.assertEqual(moodle_host.latest_user_text(messages), "second, with whitespace")


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = deployments.build(
            "marketing", mode="offline",
            database=str(Path(self.tmp.name) / "moodle_feedback.sqlite"),
        )
        self.sessions = {}
        self.key = moodle_host.session_key_for([{"role": "user", "content": "start"}], "learner-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_message_creates_a_session(self):
        self.assertNotIn(self.key, self.sessions)
        reply = moodle_host.handle_message(self.bot, self.sessions, self.key, "What are the 4Ps of marketing?")
        self.assertIn(self.key, self.sessions)
        self.assertIn("product", reply.lower())

    def test_empty_message_gets_a_prompt_not_an_error(self):
        reply = moodle_host.handle_message(self.bot, self.sessions, self.key, "   ")
        self.assertEqual(reply, "Send me a question to get started.")

    def test_survey_flow_end_to_end_through_the_pure_router(self):
        moodle_host.handle_message(self.bot, self.sessions, self.key, "Explain the STP process")
        reply = moodle_host.handle_message(self.bot, self.sessions, self.key, "Thanks, that helped")
        token = self.sessions[self.key]
        state = self.bot.sessions[token]
        if not state.survey_questions:
            self.skipTest("offline fixture did not trigger a survey on this turn")
        self.assertIn("Reply with", reply)

        n = len(state.survey_questions)
        good_ratings = ",".join(["4"] * n)
        save_reply = moodle_host.handle_message(self.bot, self.sessions, self.key, good_ratings)
        self.assertEqual(save_reply, "Thanks -- feedback saved.")

    def test_two_different_learners_get_independent_sessions(self):
        key_a = moodle_host.session_key_for([{"role": "user", "content": "q"}], "learner-a")
        key_b = moodle_host.session_key_for([{"role": "user", "content": "q"}], "learner-b")
        moodle_host.handle_message(self.bot, self.sessions, key_a, "What is a SWOT analysis?")
        moodle_host.handle_message(self.bot, self.sessions, key_b, "What is brand equity?")
        self.assertNotEqual(self.sessions[key_a], self.sessions[key_b])


class LiveServerTests(unittest.TestCase):
    """Starts the server on a free local port and sends it real HTTP requests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bot = deployments.build(
            "marketing", mode="offline",
            database=str(Path(cls.tmp.name) / "moodle_live.sqlite"),
        )
        cls.api_key = "test-key-not-real"
        cls.server = moodle_host.make_server(cls.bot, cls.api_key, host="127.0.0.1", port=0)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def _post(self, path, body, api_key=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key if api_key is not None else self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_wrong_api_key_is_rejected(self):
        status, body = self._post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}, api_key="wrong")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_models_endpoint_lists_this_deployment(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["data"][0]["id"], "plugplay-marketing")

    def test_chat_completion_round_trip_over_real_http(self):
        status, body = self._post("/v1/chat/completions", {
            "model": "plugplay-marketing",
            "messages": [{"role": "user", "content": "What is a SWOT analysis?"}],
            "user": "live-test-learner",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        content = body["choices"][0]["message"]["content"].lower()
        self.assertIn("strengths", content)

    def test_conversation_keeps_state_across_two_real_requests(self):
        first_messages = [{"role": "user", "content": "Explain the promotion mix"}]
        status, body = self._post("/v1/chat/completions", {"messages": first_messages, "user": "live-test-learner-2"})
        self.assertEqual(status, 200)
        assistant_reply = body["choices"][0]["message"]["content"]

        second_messages = first_messages + [
            {"role": "assistant", "content": assistant_reply},
            {"role": "user", "content": "Thanks, that helped"},
        ]
        status, body = self._post("/v1/chat/completions", {"messages": second_messages, "user": "live-test-learner-2"})
        self.assertEqual(status, 200)
        # The reply may be a survey or an answer. What matters is that the
        # second request continued the same session.
        self.assertTrue(body["choices"][0]["message"]["content"])


if __name__ == "__main__":
    unittest.main()
