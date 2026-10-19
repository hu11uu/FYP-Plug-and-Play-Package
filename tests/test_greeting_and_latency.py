"""Tests for the October 2026 algorithm fixes, run through the package:

1. the greeting names the deployed course instead of a hardcoded "SC1003";
2. the latency fix (helper/llm_parallel.py) starts independent model calls
   together but leaves every reply and survey decision unchanged, and an
   error in a prefetched call still surfaces exactly where it used to.
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import providers  # noqa: E402
from deployments import build  # noqa: E402
from helper import llm_parallel  # noqa: E402

SCRIPT = [
    "What is the mean?",
    "Explain variance",
    "Help with variance",
    ("scenario", "Estimate from a sample"),
    "Explain the median and outliers",
    "Who won the World Cup?",
    "Compare mean and median",
    "Thanks, that makes sense",
]


def run_script(bot, wrapping_up=True):
    from helper import survey_gen
    survey_gen.search_all.clear()  # Process-wide question-bank cache: start each run cold, so logs match.
    token = bot.new_session()
    bot.sessions[token].openai_llm.wrapping_up = wrapping_up
    transcript = []
    for step in SCRIPT:
        state = bot.sessions[token]
        if state.survey_questions:
            bot.submit_survey(token, [4] * len(state.survey_questions))
        if isinstance(step, tuple):
            snap = bot.chat(token, "x", scenario=step[1])
        else:
            snap = bot.chat(token, step)
        transcript.append((snap["messages"][-1]["content"], snap["pending_scenarios"],
                           snap["survey_questions"], snap["follow_up_suggestions"],
                           snap["events"][-1]["decisions"]))
    return transcript


class GreetingTests(unittest.TestCase):
    def test_greeting_names_each_deployed_course(self):
        expected = {
            "statistics": "STAT-DEMO: introductory statistics",
            "software_engineering": "SE-DEMO: introductory software engineering",
            "marketing": "MKT-DEMO: introductory marketing",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, label in expected.items():
                bot = build(name, database=str(Path(tmp) / f"{name}.sqlite"))
                reply = bot.chat(bot.new_session(), "Hi")["messages"][-1]["content"]
                self.assertEqual(reply, f"Hello! How can I help you with {label} today?")
                self.assertNotIn("SC1003", reply)

    def test_missing_or_long_context_falls_back_to_this_course(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = build("statistics", database=str(Path(tmp) / "s.sqlite"))
            for context in ("", "x" * 200):
                bot.chatbot_context = context
                reply = bot.chat(bot.new_session(), "hello")["messages"][-1]["content"]
                self.assertEqual(reply, "Hello! How can I help you with this course today?")


class LatencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_invoke = providers.OfflineProvider.invoke
        self.original_flag = llm_parallel.PARALLEL_LLM_CALLS

    def tearDown(self):
        providers.OfflineProvider.invoke = self.original_invoke
        llm_parallel.PARALLEL_LLM_CALLS = self.original_flag
        self.tmp.cleanup()

    def bot(self, tag):
        return build("statistics", database=str(Path(self.tmp.name) / f"{tag}.sqlite"))

    def test_parallel_and_sequential_give_identical_replies_and_decisions(self):
        llm_parallel.PARALLEL_LLM_CALLS = False
        sequential = run_script(self.bot("seq"))
        llm_parallel.PARALLEL_LLM_CALLS = True
        parallel = run_script(self.bot("par"))
        self.assertEqual(sequential, parallel)

    def test_independent_calls_actually_overlap(self):
        active, peak, lock = [0], [0], threading.Lock()
        original = self.original_invoke

        def slow(provider, prompt):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                time.sleep(0.05)
                return original(provider, prompt)
            finally:
                with lock:
                    active[0] -= 1

        providers.OfflineProvider.invoke = slow
        bot = self.bot("overlap")
        token = bot.new_session()
        bot.chat(token, "What is the mean?")
        self.assertGreaterEqual(peak[0], 3, "title, intent and domain-filter calls should run together")

    def test_error_in_a_prefetched_call_surfaces_like_before(self):
        original = self.original_invoke

        def failing_domain_filter(provider, prompt):
            if "domain filter" in providers.prompt_text(prompt):
                raise OSError("simulated model outage")
            return original(provider, prompt)

        providers.OfflineProvider.invoke = failing_domain_filter
        results = []
        for flag in (False, True):
            llm_parallel.PARALLEL_LLM_CALLS = flag
            bot = self.bot(f"err{flag}")
            snap = bot.chat(bot.new_session(), "What is the mean?")
            results.append((snap["messages"][-1]["content"], snap["last_error"]))
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[1][1], "the host should report the failure, as before")


if __name__ == "__main__":
    unittest.main()
