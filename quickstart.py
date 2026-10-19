"""Quickstart: use the plug-and-play package from your own Python code.
Save this file in the package folder (next to deployments.py) and run:
    python3 quickstart.py
"""
import textwrap

import deployments

bot = deployments.build("statistics", mode="offline")  # "azure" = live model
token = bot.new_session()                                 # one session per learner


def send(text, scenario=None):
    snap = bot.chat(token, text, scenario=scenario)       # answer + survey decision
    if snap["pending_scenarios"]:                         # ambiguous question
        print("BOT asks which one you mean:", snap["pending_scenarios"])
        return snap
    print("BOT    :", textwrap.shorten(snap["messages"][-1]["content"], 70))
    reasons = snap["events"][-1]["decisions"]             # the algorithm's reasons
    key = [r for r in reasons
           if "triggered" in r or "not ready" in r or "skipped" in r]
    print("  why  :", (key or reasons or ["(no survey check this turn)"])[-1])
    if snap["survey_questions"]:                          # the decision: prompt now
        for i, q in enumerate(snap["survey_questions"], 1):
            print(f"  Q{i}   : {q}")
    return snap


print("STUDENT: Can you compare the mean and median for me?")
send("Can you compare the mean and median for me?")

print("STUDENT: Thanks, that helped!")
snap = send("Thanks, that helped!")
if snap["survey_questions"]:
    result = bot.submit_survey(token, [5] * len(snap["survey_questions"]))
    print("SAVED  :", result["saved"],
          "| surveys this session:", result["surveys_shown_count"])

print("STUDENT: Help with variance")
snap = send("Help with variance")
if snap["pending_scenarios"]:
    choice = snap["pending_scenarios"][0]
    print("STUDENT chooses:", choice)
    send("Help with variance", scenario=choice)
