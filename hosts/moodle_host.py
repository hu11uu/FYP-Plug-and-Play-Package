"""Moodle host.

Moodle's chat block (block_ai_chat) gets its model through local_ai_manager,
which can call any OpenAI-compatible endpoint. This file is that endpoint, a
small server for the OpenAI Chat Completions API that passes each message to
the package, so no Moodle plugin has to be written. It checks an API key on
every request, since Moodle calls it from outside the machine.

Setup, with block_ai_chat and local_ai_manager installed in Moodle:
  1. cp moodle.env.example moodle.env   # set your own MOODLE_BACKEND_API_KEY, never commit this file
  2. python3 hosts/moodle_host.py --deployment marketing --port 8767
  3. Expose the port over HTTPS, for example with `ngrok http 8767`
     (local_ai_manager accepts only https:// endpoints).
  4. In local_ai_manager, add a GPT connector with the endpoint
     https://<your-tunnel>/v1/chat/completions, the same API key, and the
     model name this script prints at start-up (also at GET /v1/models).
  5. Add the AI chat block to a course.

The routing function, handle_message(), takes no HTTP objects, so
tests/test_moodle_host.py tests it without a server or Moodle.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployments  # noqa: E402


def load_env_file(path):
    """Read KEY=VALUE lines from a local file into the environment, if the file exists.

    Values already set in the environment are kept.
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


def diag_line(key, is_new_session, end_user, messages):
    """Build one line for the optional diagnostic log (--diag-log).

    It records a shortened session key, whether a new session was started,
    Moodle's user field, the message count, the length and first 40 characters
    of the first message, and the last message's length. That is enough to see
    when a conversation's key changes without logging whole conversations. This
    log showed that Moodle trims long histories, which changes the key.
    """
    first_user_text = ""
    for message in messages:
        if message.get("role") == "user":
            first_user_text = message.get("content", "")
            break
    last_text = (messages[-1].get("content") or "") if messages else ""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    return (
        f"{ts} | key={key[:12]} | new_session={is_new_session} | "
        f"user_field={end_user!r} | msg_count={len(messages)} | "
        f"first_msg_len={len(first_user_text)} first_msg_prefix={first_user_text[:40]!r} | "
        f"last_msg_len={len(last_text)}\n"
    )


def write_diag_line(diag_log_path, line):
    # Opened and closed on every call, so nothing is lost if the server stops
    # mid-test, and a failed write never breaks the request itself.
    try:
        with open(diag_log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def session_key_for(messages, end_user):
    """Work out which conversation a request belongs to.

    The Chat Completions API sends the whole history with every request and no
    conversation ID. The key is a hash of the first user message and Moodle's
    user field, when it is sent. It stays the same while the first message
    does, and it contains no student text.
    """
    first_user_text = ""
    for message in messages:
        if message.get("role") == "user":
            first_user_text = message.get("content", "")
            break
    raw = f"{end_user or ''}\x00{first_user_text}"
    return hashlib.sha256(raw.encode()).hexdigest()


def latest_user_text(messages):
    """Return the new message, which is the last entry and must come from the user.

    Earlier entries are history the session already has, so they are not
    passed to the algorithm again.
    """
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("Expected the last message to have role 'user'.")
    return (messages[-1].get("content") or "").strip()


def handle_message(bot, sessions, session_key, text):
    """Route one message, in the same way as hosts/telegram_host.py.

    While a survey is pending, the reply is read as ratings. Otherwise the
    message goes to chat().
    """
    if session_key not in sessions:
        sessions[session_key] = bot.new_session()
    token = sessions[session_key]
    state = bot.sessions[token]
    text = (text or "").strip()
    if not text:
        return "Send me a question to get started."

    if state.survey_questions:
        nums = re.findall(r"[1-5]", text)
        if len(nums) != len(state.survey_questions):
            # Show the questions again, not just the instruction. If Moodle
            # timed out on the turn that showed the survey, the learner never
            # saw the questions.
            return (
                survey_prompt(state.survey_questions)
                + f"\n\n(Please reply with exactly {len(state.survey_questions)} numbers 1-5, comma-separated.)"
            )
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


def chat_completion_envelope(reply_text, model_name):
    """Wrap a reply in the Chat Completions response format.

    Token counts are 0 because this server does not count tokens.
    """
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply_text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def openai_error(message, status_name="invalid_request_error"):
    return {"error": {"message": message, "type": status_name}}


def make_server(bot, api_key, host="127.0.0.1", port=8767, diag_log_path=None):
    model_name = f"plugplay-{getattr(bot, 'ui', {}).get('deployment', 'custom')}"
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            # A bearer key is required, since Moodle calls this server from
            # outside the machine.
            header = self.headers.get("Authorization", "")
            return header == f"Bearer {api_key}"

        def do_GET(self):
            if self.path == "/v1/models":
                if not self.authorized():
                    return self.respond(HTTPStatus.UNAUTHORIZED, openai_error("Invalid API key.", "authentication_error"))
                return self.respond(HTTPStatus.OK, {
                    "object": "list",
                    "data": [{"id": model_name, "object": "model", "owned_by": "plugplay-package"}],
                })
            self.respond(HTTPStatus.NOT_FOUND, openai_error("Not found."))

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                return self.respond(HTTPStatus.NOT_FOUND, openai_error("Not found."))
            if not self.authorized():
                return self.respond(HTTPStatus.UNAUTHORIZED, openai_error("Invalid API key.", "authentication_error"))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(length))
                messages = payload.get("messages")
                if not isinstance(messages, list) or not messages:
                    raise ValueError("Expected a non-empty 'messages' array.")
                text = latest_user_text(messages)
                end_user = payload.get("user")
                key = session_key_for(messages, end_user)
                if diag_log_path is not None:
                    is_new_session = key not in sessions
                    write_diag_line(diag_log_path, diag_line(key, is_new_session, end_user, messages))
                reply = handle_message(bot, sessions, key, text)
                self.respond(HTTPStatus.OK, chat_completion_envelope(reply, model_name))
            except (ValueError, TypeError) as exc:
                self.respond(HTTPStatus.BAD_REQUEST, openai_error(str(exc)))
            except Exception:
                self.respond(HTTPStatus.INTERNAL_SERVER_ERROR, openai_error("Backend error; the request was not completed."))

    return HTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", default="software_engineering", choices=deployments.available() or ["software_engineering"])
    parser.add_argument("--model", choices=("offline", "azure"), default="offline")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Use 0.0.0.0 to be reachable from a real Moodle server; keep 127.0.0.1 plus a tunnel for local testing.")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--env-file", default=str(deployments.ROOT / "moodle.env"))
    parser.add_argument("--azure-env-file", default=str(deployments.ROOT / "azure.env"))
    parser.add_argument(
        "--diag-log", default=None, metavar="PATH",
        help="Append one plain-text diagnostic line per request to this file "
             "(session key prefix, whether a new session was started, the "
             "'user' field Moodle sent if any, message count, and the first "
             "message's length/prefix). Off by default -- opt in with e.g. "
             "--diag-log moodle_diag.log while grid-testing against Moodle, "
             "to check whether a mid-conversation session reset is really "
             "caused by the resent history changing shape.",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    api_key = os.environ.get("MOODLE_BACKEND_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "Set MOODLE_BACKEND_API_KEY (e.g. in moodle.env, copied from moodle.env.example) "
            "before running the live server -- this is the API key value you'll also enter "
            "into Moodle's local_ai_manager connector settings. "
            "The routing logic itself can be exercised without one: see tests/test_moodle_host.py."
        )
    if args.model == "azure":
        load_env_file(Path(args.azure_env_file))
        from providers import AzureProvider
        AzureProvider()

    bot = deployments.build(args.deployment, mode=args.model)
    server = make_server(bot, api_key, host=args.host, port=args.port, diag_log_path=args.diag_log)
    model_name = f"plugplay-{getattr(bot, 'ui', {}).get('deployment', args.deployment)}"
    print(f"Plug-and-play Moodle/OpenAI-compatible host: http://{args.host}:{server.server_port}/v1 "
          f"(deployment={args.deployment}, model={args.model}, model-id-for-moodle={model_name})", flush=True)
    if args.diag_log:
        print(f"Diagnostic logging ON -- appending per-request session-key lines to {args.diag_log}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
