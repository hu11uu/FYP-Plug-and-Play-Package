"""Telegram host.

Runs any subject as a Telegram bot through python-telegram-bot. Telegram has
no built-in survey form, so a pending survey is sent as a text message and
the learner replies with numbers from 1 to 5.

The routing functions take no Telegram objects, so
tests/test_telegram_host.py tests them without a bot token or network.

Setup:
  1. Message @BotFather on Telegram, send /newbot and copy the token.
  2. cp telegram.env.example telegram.env   # paste the token after TELEGRAM_BOT_TOKEN=, never commit this file
  3. python3 hosts/telegram_host.py --deployment software_engineering

For a live model, copy azure.env.example to azure.env, fill it in and add
--model azure.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployments  # noqa: E402
from providers import AzureProvider  # noqa: E402


def load_env_file(path):
    """Read KEY=VALUE lines from a local file into the environment, if the file exists.

    Values already set in the environment are kept. python-dotenv is not used
    because bridge.py replaces it with a stand-in.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def survey_prompt(questions):
    lines = ["One quick check before we continue:"]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append(f'Reply with {len(questions)} numbers from 1-5, comma-separated (e.g. "4,5").')
    return "\n".join(lines)


def handle_message(bot, sessions, chat_id, text):
    """Route one message from a chat and return the reply text.

    Each Telegram chat_id maps to one session token. While a survey is
    pending, the reply is read as ratings.
    """
    if chat_id not in sessions:
        sessions[chat_id] = bot.new_session()
    token = sessions[chat_id]
    state = bot.sessions[token]
    text = (text or "").strip()
    if not text:
        return "Send me a question to get started."

    if state.survey_questions:
        nums = re.findall(r"[1-5]", text)
        if len(nums) != len(state.survey_questions):
            return f"Please reply with exactly {len(state.survey_questions)} numbers 1-5, comma-separated."
        result = bot.submit_survey(token, [int(n) for n in nums])
        if result["saved"]:
            return "Thanks -- feedback saved."
        if state.get("_survey_save_gave_up"):
            return "Original algorithm discarded the responses after its third failed save."
        return "Save failed. Please resend your ratings to retry."

    snap = bot.chat(token, text)
    reply = snap["messages"][-1]["content"]
    if snap["survey_questions"]:
        reply = reply + "\n\n" + survey_prompt(snap["survey_questions"])
    return reply


def handle_start(bot, sessions, chat_id):
    sessions[chat_id] = bot.new_session()
    return f"New session started ({bot.conversation_title}). Ask me anything about this topic."


def build_application(token, bot):
    """Connect the routing functions to python-telegram-bot.

    The library is imported here, so the rest of the file can be tested
    without it installed.
    """
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

    sessions = {}

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reply = handle_start(bot, sessions, update.effective_chat.id)
        await update.message.reply_text(reply)

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reply = handle_message(bot, sessions, update.effective_chat.id, update.message.text)
        await update.message.reply_text(reply)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", default="software_engineering", choices=deployments.available() or ["software_engineering"])
    parser.add_argument("--model", choices=("offline", "azure"), default="offline")
    parser.add_argument("--env-file", default=str(deployments.ROOT / "telegram.env"))
    parser.add_argument("--azure-env-file", default=str(deployments.ROOT / "azure.env"))
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN (e.g. in telegram.env, copied from telegram.env.example) "
            "to a token from @BotFather before running the live bot. "
            "The adapter logic itself can be exercised without a token: "
            "see tests/test_telegram_host.py."
        )
    if args.model == "azure":
        load_env_file(Path(args.azure_env_file))  # read azure.env before checking it
        AzureProvider()  # check the four FYP_AZURE_* variables now

    bot = deployments.build(args.deployment, mode=args.model)
    application = build_application(token, bot)
    print(f"Plug-and-play Telegram host: polling as configured bot (deployment={args.deployment}, model={args.model})", flush=True)
    application.run_polling()
