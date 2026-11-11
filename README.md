# Survey-triggering algorithm: plug-and-play package

**Video walkthrough:** [Setup on Gradio, Telegram and Streamlit](https://youtu.be/AOgoGiXkmM4)

This package comes from the NTU Final Year Project *An Intelligent,
Multi-Tiered Survey-Triggering System for a Conversational Feedback
Chatbot*. It runs the project's survey-triggering algorithm, unchanged,
behind a small shared core, so another chatbot only needs a thin adapter.
It includes three subjects (statistics, software engineering and
marketing), adapters for Gradio, Telegram, Moodle and Streamlit, and a local
browser host for testing.

The original Streamlit chatbot's code (the features described in Chapters
3 to 5 of the report) is in `vendor/FYP-project/`, copied unchanged. It can
be run with `streamlit run app.py` from that folder, given the project's
Azure and MongoDB credentials. Everything else in this repository runs
without them.

The 18 files in `vendor/FYP-project/` are exact copies of the project's
files. Their SHA-256 hashes are stored in `source-manifest.json` and checked
at start-up, so the package will not run a changed copy of the algorithm.
When the project changes, run `python sync.py <path-to-FYP-project> .` to
copy the files in and update the hashes, then run the tests again.

## Layout

| Path | What it is |
|---|---|
| `core.py` | `SurveyChatbot`, the shared core: sessions, chat and survey submission |
| `deployments.py` | Builds a `SurveyChatbot` from a subject folder in `deployments/` |
| `bridge.py` | Stand-ins that let the original Streamlit code run outside Streamlit |
| `providers.py` | The offline test model and the live Azure OpenAI model |
| `hosts/` | One adapter per platform, plus `template_host.py` for a new one |
| `deployments/<name>/` | One folder per subject, with `config.json` and `content/course.json` |
| `content/survey-bank.json` | The 34 feedback questions, shared by every subject |
| `vendor/FYP-project/` | The original project's code, unchanged |
| `tests/` | 38 automated tests |
| `quickstart.py` | A short example of using the package from your own code |
| `show_feedback.py` | Prints the survey answers saved for a subject |
| `sync.py` | Copies the algorithm files from the project and updates the hashes |
| `measure_latency.py` | Measures reply time with concurrent model calls on and off |

## Hosts

| Host | Platform | How messages arrive |
|---|---|---|
| `hosts/http_server.py` | Local browser page, for testing | HTTP on 127.0.0.1 |
| `hosts/gradio_host.py` | Gradio | Gradio chat callback |
| `hosts/telegram_host.py` | Telegram | Long polling through `python-telegram-bot` |
| `hosts/moodle_host.py` | Moodle | An OpenAI-compatible endpoint that `block_ai_chat` calls through `local_ai_manager` |
| `hosts/streamlit_host.py` | Streamlit | A separate Streamlit process that calls `hosts/http_server.py` over local HTTP |

The hosts contain no survey logic. Each one passes messages to
`bot.chat()` and survey ratings to `bot.submit_survey()`, then shows what
comes back. Gradio, Telegram and Moodle have no built-in survey form, so a
survey is shown as a text message and the learner replies with numbers
from 1 to 5. The Streamlit host shows the survey as a form, and the scenario
options and follow-up suggestions as buttons.

Streamlit cannot import the package directly, because `bridge.py` puts a
stand-in in place of the `streamlit` module so the original algorithm can
run outside Streamlit. The Streamlit host therefore runs as its own process
and talks to the package's local server.

## Run it

Python 3.10 or later. Offline mode needs no extra libraries, credentials or
network.

```sh
# Local browser page
python3 hosts/http_server.py --deployment statistics

# Gradio
pip install gradio
python3 hosts/gradio_host.py --deployment software_engineering

# Telegram (needs your own bot token)
pip install python-telegram-bot
cp telegram.env.example telegram.env   # paste the token from @BotFather
python3 hosts/telegram_host.py --deployment software_engineering

# Moodle (needs a Moodle site with block_ai_chat and local_ai_manager)
cp moodle.env.example moodle.env       # set your own shared key
python3 hosts/moodle_host.py --deployment marketing --port 8767
ngrok http 8767                        # local_ai_manager accepts only https:// endpoints

# Streamlit (two terminals)
pip install streamlit
python3 hosts/http_server.py --deployment statistics   # terminal 1: the package
streamlit run hosts/streamlit_host.py                  # terminal 2: the chat page
```

For live answers from Azure OpenAI, copy `azure.env.example` to
`azure.env`, fill it in, and add `--model azure` to any host command.
`hosts/http_server.py` reads the same four variables from the environment
instead. Appendix A of the report has the full setup steps, including the
Moodle connector settings.

## See the feedback

Each subject saves its surveys in `runtime/<subject>.sqlite` (excluded from
git). To read them:

```sh
python3 show_feedback.py statistics
```

It prints each saved survey, newest first, with the questions, the 1-5
answers, the time and the question the learner had just asked, plus the
average rating.

## Use it from your own code, or add a platform

- `quickstart.py` is a complete example program that calls the package
  directly, with no host: `python3 quickstart.py` (offline, no credentials).
- `hosts/template_host.py` is a starting point for a new platform adapter.
  Copy it, replace the demonstration loop at the bottom with the platform's
  own receive and send calls, and pass a conversation id that stays the same
  within one conversation. `tests/test_template_host.py` tests it without
  any platform or network.
- A new subject is a new folder in `deployments/` with `config.json`
  (`conversation_title` and `chatbot_context`) and `content/course.json`
  (topics with an id, title, keywords and content).

## Tests

```sh
python3 -m unittest discover -s tests -v
```

All 38 tests run offline, with no token or network.

| File | Tests | What it covers |
|---|---|---|
| `tests/test_core.py` | 6 | Sessions, chat, survey trigger and save, input checks, separate subjects |
| `tests/test_deployments.py` | 4 | The three subjects load and stay separate, unknown names fail clearly |
| `tests/test_greeting_and_latency.py` | 5 | Course-specific greeting, and unchanged decisions with concurrent calls |
| `tests/test_moodle_host.py` | 13 | Session keys, routing, and a live local server driven with real HTTP requests |
| `tests/test_telegram_host.py` | 7 | Routing and the survey flow with stand-in chat ids |
| `tests/test_template_host.py` | 3 | The template adapter |

## October 2026 updates

Both changes were made in the project and copied in with `sync.py`.

- The greeting no longer always says "SC1003". It takes the course name
  from `chatbot_context` (`_course_label()` in `helper/response.py`).
- Model calls that do not depend on each other now run at the same time
  (`helper/llm_parallel.py`). Replies and survey decisions are unchanged.
  Set `PARALLEL_LLM_CALLS = False` in that file to run them one at a time.

## Limits

- The offline model is scripted. Its answers and judgements are fixed test
  data, not a language model's.
- The package uses SQLite and keyword search in place of the original
  MongoDB database and Azure AI Search.
- The Gradio, Telegram and Moodle adapters do not yet show follow-up
  suggestions or clarification options.
- Moodle sends no conversation ID, so when it trims a long history the
  session key changes and a new session starts.
- The algorithm has not been tested with real students.

The subject content was written for this project from the sources cited
in the report.
