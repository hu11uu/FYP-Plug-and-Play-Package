"""Tests for SurveyChatbot in core.py.

Covers sessions, chat, the survey trigger and save, input checks, and
keeping two subjects apart. Uses the offline model, so no network is needed.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import SurveyChatbot  # noqa: E402


def load_statistics_bot(database):
    course = json.loads((ROOT / "deployments/statistics/content/course.json").read_text())
    bank = json.loads((ROOT / "content/survey-bank.json").read_text())
    return SurveyChatbot(
        content_pack=course, survey_bank=bank,
        chatbot_context="STAT-DEMO: introductory statistics",
        conversation_title="Statistics practice",
        database=database, mode="offline",
    )


class SurveyChatbotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = load_statistics_bot(str(Path(self.tmp.name) / "feedback.sqlite"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_session_returns_unique_tokens(self):
        a, b = self.bot.new_session(), self.bot.new_session()
        self.assertNotEqual(a, b)
        self.assertIn(a, self.bot.sessions)
        self.assertIn(b, self.bot.sessions)

    def test_chat_calls_original_answer_and_records_history(self):
        token = self.bot.new_session()
        snap = self.bot.chat(token, "Compare mean and median")
        self.assertEqual(snap["messages"][-2], {"role": "human", "content": "Compare mean and median"})
        self.assertEqual(snap["messages"][-1]["role"], "ai")
        self.assertIn("mean", snap["messages"][-1]["content"].lower())

    def test_survey_flow_triggers_and_saves(self):
        token = self.bot.new_session()
        self.bot.chat(token, "Compare mean and median")
        snap = self.bot.chat(token, "Thanks, that helped")
        self.assertTrue(snap["survey_questions"], "offline fixture should trigger a survey here")
        ratings = [4] * len(snap["survey_questions"])
        result = self.bot.submit_survey(token, ratings)
        self.assertTrue(result["saved"])
        self.assertEqual(result["surveys_shown_count"], 1)

    def test_chat_rejects_empty_query(self):
        token = self.bot.new_session()
        with self.assertRaises(ValueError):
            self.bot.chat(token, "   ")

    def test_submit_survey_rejects_wrong_length(self):
        token = self.bot.new_session()
        self.bot.chat(token, "Compare mean and median")
        snap = self.bot.chat(token, "Thanks, that helped")
        if snap["survey_questions"]:
            with self.assertRaises(ValueError):
                self.bot.submit_survey(token, [4])  # wrong length on purpose

    def test_different_content_packs_are_isolated(self):
        # Two bots pointed at different topics must not see each other's content.
        mk_course = json.loads((ROOT / "deployments/marketing/content/course.json").read_text())
        bank = json.loads((ROOT / "content/survey-bank.json").read_text())
        mk_bot = SurveyChatbot(
            content_pack=mk_course, survey_bank=bank,
            chatbot_context="MKT-DEMO: introductory marketing",
            conversation_title="Marketing practice",
            database=str(Path(self.tmp.name) / "mk_feedback.sqlite"), mode="offline",
        )
        token = mk_bot.new_session()
        snap = mk_bot.chat(token, "What are the 4Ps of marketing?")
        self.assertIn("product", snap["messages"][-1]["content"].lower())
        self.assertNotIn("mean", snap["messages"][-1]["content"].lower())


if __name__ == "__main__":
    unittest.main()
