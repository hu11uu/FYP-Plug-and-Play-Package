"""Measure reply time per message, with the latency fix on and off.

    python measure_latency.py --deployment statistics --model azure
    python measure_latency.py --deployment statistics --delay 1.0   # offline, 1 s per call
    python measure_latency.py --deployment statistics --model azure --rounds 3 "Explain variance" "Compare mean and median"

With --model azure, the four FYP_AZURE_* variables are read from azure.env
(or --env-file); variables already set in the environment take priority.
Each round runs the same questions in a fresh session twice: once with
PARALLEL_LLM_CALLS off (original sequential calls) and once with it on, and
prints seconds and model calls per message. Survey prompts that appear are
answered with 4s so the script can continue.
"""
import argparse
import os
import statistics
import threading
import tempfile
import time
from pathlib import Path

from deployments import build
from helper import llm_parallel

DEFAULT_FOLLOW_UPS = ["Can you give an example?", "Thanks, that makes sense"]


class CountingModel:
    """Wraps the session's model object to count calls; does not change them."""
    def __init__(self, inner, delay=0.0):
        self.inner, self.calls, self.lock, self.delay = inner, 0, threading.Lock(), delay

    def invoke(self, prompt):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)  # Offline only: stand-in for network/model time.
        return self.inner.invoke(prompt)


def run(bot, questions, parallel, delay):
    llm_parallel.PARALLEL_LLM_CALLS = parallel
    token = bot.new_session()
    state = bot.sessions[token]
    state.openai_llm = CountingModel(state.openai_llm, delay)
    rows = []
    for q in questions:
        if state.survey_questions:
            bot.submit_survey(token, [4] * len(state.survey_questions))
        before = state.openai_llm.calls
        start = time.perf_counter()
        snap = bot.chat(token, q)
        if snap["pending_scenarios"]:  # Ambiguous question: pick the first option, as a student would.
            snap = bot.chat(token, q, scenario=snap["pending_scenarios"][0])
        rows.append((q, time.perf_counter() - start, state.openai_llm.calls - before))
    return rows


def load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deployment", default="statistics")
    ap.add_argument("--model", choices=["offline", "azure"], default="offline")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="offline only: pretend each model call takes this many seconds")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parent / "azure.env"))
    ap.add_argument("questions", nargs="*")
    args = ap.parse_args()
    if args.model == "azure":
        load_env_file(Path(args.env_file))

    with tempfile.TemporaryDirectory() as tmp:
        bot = build(args.deployment, mode=args.model, database=str(Path(tmp) / "latency.sqlite"))
        questions = args.questions or (bot.ui["sample_questions"] + DEFAULT_FOLLOW_UPS)
        results = {False: [], True: []}
        for _ in range(args.rounds):
            for parallel in (False, True):
                results[parallel].append(run(bot, questions, parallel, args.delay if args.model == "offline" else 0.0))

    print(f"{'message':42} {'sequential':>12} {'parallel':>12} {'calls':>7}")
    for i, q in enumerate(questions):
        seq = statistics.median(r[i][1] for r in results[False])
        par = statistics.median(r[i][1] for r in results[True])
        calls = results[True][0][i][2]
        print(f"{q[:42]:42} {seq:11.2f}s {par:11.2f}s {calls:7}")
    seq_total = statistics.median(sum(x[1] for x in r) for r in results[False])
    par_total = statistics.median(sum(x[1] for x in r) for r in results[True])
    change = (1 - par_total / seq_total) * 100 if seq_total else 0
    print(f"{'TOTAL':42} {seq_total:11.2f}s {par_total:11.2f}s   ({change:.0f}% faster)")


if __name__ == "__main__":
    main()
