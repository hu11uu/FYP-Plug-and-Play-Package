"""Tests for the template adapter: no platform, network or credentials needed."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hosts"))
import deployments
from template_host import handle_message


class TemplateHostTests(unittest.TestCase):
    def setUp(self):
        db = Path(tempfile.mkdtemp()) / "test.sqlite"
        self.bot = deployments.build("statistics", mode="offline", database=str(db))
        self.sessions = {}

    def say(self, text, conversation="c1"):
        return handle_message(self.bot, self.sessions, conversation, text)

    def test_ambiguous_question_offers_options_instead_of_echoing(self):
        reply = self.say("Help with variance")
        self.assertIn("Which of these do you mean?", reply)
        self.assertNotEqual(reply.strip(), "Help with variance")

    def test_choice_answers_then_survey_is_saved(self):
        self.say("Help with variance")
        reply = self.say("1")
        self.assertIn("population variance", reply)
        self.assertIn("One quick check", reply)
        self.assertEqual(self.say("4, 5"), "Thanks, feedback saved.")

    def test_conversations_are_independent(self):
        self.say("Help with variance", conversation="a")
        reply = self.say("Compare the mean and median", conversation="b")
        self.assertNotIn("Which of these do you mean?", reply)


if __name__ == "__main__":
    unittest.main()
