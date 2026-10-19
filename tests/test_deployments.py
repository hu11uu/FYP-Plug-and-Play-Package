"""Tests for deployments.py.

The three subjects build and answer a question from their own content, they
do not share a database, and an unknown name gives a clear error.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import deployments  # noqa: E402


class DeploymentDiscoveryTests(unittest.TestCase):
    def test_available_lists_all_three_shipped_deployments(self):
        self.assertEqual(set(deployments.available()), {"statistics", "software_engineering", "marketing"})

    def test_build_unknown_deployment_raises_with_known_names_listed(self):
        with self.assertRaises(ValueError) as ctx:
            deployments.build("not_a_real_deployment")
        message = str(ctx.exception)
        self.assertIn("not_a_real_deployment", message)
        for name in ("statistics", "software_engineering", "marketing"):
            self.assertIn(name, message)

    def test_each_deployment_builds_and_answers_a_topic_question(self):
        cases = {
            "statistics": ("Compare mean and median", "mean"),
            "software_engineering": ("What are the levels of software testing?", "unit testing"),
            "marketing": ("What are the 4Ps of marketing?", "product"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (query, expected_word) in cases.items():
                bot = deployments.build(name, mode="offline", database=str(Path(tmp) / f"{name}.sqlite"))
                token = bot.new_session()
                snap = bot.chat(token, query)
                self.assertIn(expected_word, snap["messages"][-1]["content"].lower(), f"deployment={name}")

    def test_two_deployments_never_share_a_store_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = deployments.build("statistics", database=str(Path(tmp) / "a.sqlite"))
            b = deployments.build("marketing", database=str(Path(tmp) / "b.sqlite"))
            self.assertIsNotNone(a.store)
            self.assertIsNotNone(b.store)
            self.assertIsNot(a.store, b.store)


if __name__ == "__main__":
    unittest.main()
