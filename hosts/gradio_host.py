"""Gradio host.

Runs any subject in a Gradio chat window. Gradio has no built-in form for a
multi-question survey, so a pending survey is shown as text and the learner
replies with numbers from 1 to 5.

Offline (no network):
    python3 hosts/gradio_host.py --deployment statistics

With a live Azure OpenAI model:
    cp azure.env.example azure.env   # fill in your own values, never commit this file
    python3 hosts/gradio_host.py --deployment software_engineering --model azure
"""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# bridge.install() refuses to run if the real dotenv is already loaded, and
# Gradio loads it when imported. Importing deployments first runs install()
# before Gradio is imported.
import deployments  # noqa: E402  (runs bridge.install() through core.py)
from providers import AzureProvider  # noqa: E402

import gradio as gr  # noqa: E402  (must come after install())


def load_env_file(path):
    """Read KEY=VALUE lines from a local file into the environment.

    Used only with --model azure, and only if the file exists. Values already
    set in the environment are kept. python-dotenv is not used because
    bridge.py replaces it with a stand-in.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--deployment", default="statistics", choices=deployments.available() or ["statistics"])
parser.add_argument("--model", choices=("offline", "azure"), default="offline")
parser.add_argument("--port", type=int, default=7861)
parser.add_argument("--env-file", default=str(deployments.ROOT / "azure.env"))
args = parser.parse_args()

if args.model == "azure":
    load_env_file(Path(args.env_file))
    AzureProvider()  # check the four FYP_AZURE_* variables now

BOT = deployments.build(args.deployment, mode=args.model)
SESSIONS = {}  # Gradio session id -> package session token


def render_history(state):
    return [{"role": "user" if m["role"] == "human" else "assistant", "content": m["content"]}
            for m in state.messages]


def survey_prompt(questions):
    lines = ["One quick check before we continue:"]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append(f'Reply with {len(questions)} numbers from 1-5, comma-separated (e.g. "4,5").')
    return "\n".join(lines)


def chat_turn(message, session_id):
    """Pass one message to the chatbot and turn the result back into chat text.

    While a survey is pending, the reply is read as ratings and sent to
    submit_survey().
    """
    if not session_id or session_id not in SESSIONS:
        session_id = secrets.token_urlsafe(12)
        SESSIONS[session_id] = BOT.new_session()
    token = SESSIONS[session_id]
    state = BOT.sessions[token]
    message = (message or "").strip()
    if not message:
        return render_history(state), session_id, ""

    if state.survey_questions:
        nums = re.findall(r"[1-5]", message)
        if len(nums) != len(state.survey_questions):
            state.messages.append({"role": "human", "content": message})
            state.messages.append({"role": "ai", "content": f"Please reply with exactly {len(state.survey_questions)} numbers 1-5, comma-separated."})
            return render_history(state), session_id, ""
        state.messages.append({"role": "human", "content": message})
        result = BOT.submit_survey(token, [int(n) for n in nums])
        if result["saved"]:
            note = "Thanks -- feedback saved."
        elif state.get("_survey_save_gave_up"):
            note = "Original algorithm discarded the responses after its third failed save."
        else:
            note = "Save failed. Please resend your ratings to retry."
        state.messages.append({"role": "ai", "content": note})
        return render_history(state), session_id, ""

    BOT.chat(token, message)
    if state.survey_questions:
        state.messages.append({"role": "ai", "content": survey_prompt(state.survey_questions)})
    return render_history(state), session_id, ""


with gr.Blocks(title=f"{BOT.conversation_title} -- plug-and-play package demo") as demo:
    gr.Markdown(
        f"### {BOT.conversation_title} (plug-and-play package demo)\n"
        "Same frozen FYP algorithm, now loaded through the shared plug-and-play "
        f"core (`core.py` + `deployments.py`) for the `{args.deployment}` deployment.\n\n"
        f"**Model: {'Azure OpenAI (live)' if args.model == 'azure' else 'offline fixture (scripted, not a real model)'}**"
    )
    session_id_state = gr.State("")
    chatbot = gr.Chatbot(label="Chat")  # gradio 6.x: messages format (role/content dicts) is the default
    msg = gr.Textbox(label="Message", placeholder=f"Ask a question about {BOT.conversation_title.lower()}...")
    msg.submit(chat_turn, [msg, session_id_state], [chatbot, session_id_state, msg], api_name="chat")

if __name__ == "__main__":
    print(f"Plug-and-play Gradio host: http://127.0.0.1:{args.port} (deployment={args.deployment}, model={args.model})", flush=True)
    demo.launch(server_name="127.0.0.1", server_port=args.port)
