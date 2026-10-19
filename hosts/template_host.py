"""Template adapter for a new platform. Copy into hosts/ and replace the demo
loop at the bottom with the platform's own receive/send calls."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployments  # import before any other framework: it installs the bridge


def survey_prompt(questions):
    lines = ["One quick check before we continue:"]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append(f"Reply with {len(questions)} numbers from 1-5, "
                 'comma-separated (e.g. "4,5").')
    return "\n".join(lines)


def scenario_prompt(options):
    lines = ["Which of these do you mean?"]
    lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
    lines.append("Reply with the number of your choice.")
    return "\n".join(lines)


def handle_message(bot, sessions, conversation_id, text):
    """One inbound message in, one reply string out. No platform objects here,
    so this function can be unit-tested without the platform."""
    text = (text or "").strip()
    if conversation_id not in sessions:                   # duty 1: session identity
        sessions[conversation_id] = bot.new_session()
    token = sessions[conversation_id]
    state = bot.snapshot(token)
    if not text:
        return "Send me a question to get started."

    if state["survey_questions"]:                         # duty 2: pending survey
        ratings = [int(n) for n in re.findall(r"[1-5]", text)]
        if len(ratings) != len(state["survey_questions"]):
            return survey_prompt(state["survey_questions"])
        result = bot.submit_survey(token, ratings)
        if result["saved"]:
            return "Thanks, feedback saved."
        return "Saving failed; please send your ratings again."

    scenario = None                                       # reply to a clarification
    options = state["pending_scenarios"]
    if options and text.isdigit() and 1 <= int(text) <= len(options):
        scenario = options[int(text) - 1]

    try:                                                  # duty 3: a normal turn
        snap = bot.chat(token, text, scenario=scenario)
    except ValueError as exc:                             # duty 4: safe errors
        return str(exc)

    if snap["pending_scenarios"]:                         # no answer this turn
        return scenario_prompt(snap["pending_scenarios"])
    reply = snap["messages"][-1]["content"]
    if snap["survey_questions"]:
        reply += "\n\n" + survey_prompt(snap["survey_questions"])
    return reply


if __name__ == "__main__":
    # Stand-in for a real platform: replace with the platform's receive/send loop.
    bot = deployments.build("statistics", mode="offline")
    sessions = {}
    for message in ["Help with variance", "1", "4, 5"]:
        print("STUDENT:", message)
        reply = handle_message(bot, sessions, "demo-conversation", message)
        print("BOT    :", reply, "\n")
