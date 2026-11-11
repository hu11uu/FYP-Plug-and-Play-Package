"""Streamlit host.

Runs any subject as a Streamlit chat page. Streamlit cannot import the package
directly, because bridge.py puts a small stand-in in place of the `streamlit`
module so the original algorithm can run outside Streamlit. So this page runs
as its own process and talks to the package's local server
(hosts/http_server.py) over HTTP.

Unlike the Gradio and Telegram hosts, this page shows the survey as a form,
offers scenario options and follow-up suggestions as buttons.

Run, in two terminals:
    python3 hosts/http_server.py --deployment statistics
    streamlit run hosts/streamlit_host.py

The model (offline or Azure) is chosen when starting http_server.py. If the
server runs on another port:
    streamlit run hosts/streamlit_host.py -- --server http://127.0.0.1:8766
"""
import argparse
import http.cookiejar
import json
import urllib.error
import urllib.request

import streamlit as st

parser = argparse.ArgumentParser()
parser.add_argument("--server", default="http://127.0.0.1:8765")
args, _ = parser.parse_known_args()
SERVER = args.server.rstrip("/")


class PackageClient:
    """Calls the package's local server. One client holds one session cookie."""

    def __init__(self, server):
        self.server = server
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.server + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=300) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read() or b"{}")
            raise ValueError(body.get("error", f"Server error {exc.code}")) from None

    def new_session(self):
        return self._post("/api/session", {})

    def chat(self, text, scenario=None):
        return self._post("/api/chat", {"query": text, "scenario": scenario})

    def submit_survey(self, ratings):
        return self._post("/api/survey", {"ratings": ratings})


def start_session():
    client = PackageClient(SERVER)
    st.session_state.snap = client.new_session()
    st.session_state.client = client
    st.session_state.notice = None


def run(action):
    """Call the package, keep the new snapshot, and redraw the page."""
    try:
        with st.spinner("Thinking..."):
            st.session_state.snap = action()
    except ValueError as exc:
        st.session_state.notice = str(exc)
    st.rerun()


st.set_page_config(page_title="Plug-and-play package · Streamlit")
if "client" not in st.session_state:
    try:
        start_session()
    except urllib.error.URLError:
        st.error(f"Cannot reach the package server at {SERVER}. Start it first:\n\n"
                 "`python3 hosts/http_server.py --deployment statistics`")
        st.stop()

client, snap = st.session_state.client, st.session_state.snap
ui = snap.get("ui", {})
st.title(ui.get("display_name", "Course chat").capitalize())
st.caption(f"Plug-and-play package · Streamlit host · model: {snap.get('mode', '')}")
if st.sidebar.button("New conversation"):
    start_session()
    st.rerun()

for message in snap.get("messages") or []:
    with st.chat_message("user" if message["role"] == "human" else "assistant"):
        st.markdown(message["content"])

if st.session_state.notice:
    st.info(st.session_state.notice)
    st.session_state.notice = None
if snap.get("last_error"):
    st.warning(snap["last_error"])

questions = snap.get("survey_questions") or []
if questions:                                             # the algorithm asked for feedback
    with st.form("survey"):
        st.markdown("**One quick check before we continue** (1 = strongly disagree, 5 = strongly agree)")
        ratings = [st.radio(q, [1, 2, 3, 4, 5], index=None, horizontal=True, key=f"q{i}")
                   for i, q in enumerate(questions)]
        if st.form_submit_button("Submit feedback"):
            if None in ratings:
                st.warning("Please rate every question.")
            else:
                def submit():
                    result = client.submit_survey(ratings)
                    st.session_state.notice = ("Thanks, feedback saved." if result.get("saved")
                                               else "Saving failed. Please submit again.")
                    return result
                run(submit)

elif snap.get("pending_scenarios"):                       # ambiguous question
    st.markdown("**Which of these do you mean?**")
    for option in snap["pending_scenarios"]:
        if st.button(option, key=f"s_{option}"):
            run(lambda option=option: client.chat(option, scenario=option))

elif snap.get("follow_up_suggestions"):                   # suggested next questions
    st.caption("You could also ask:")
    for suggestion in snap["follow_up_suggestions"]:
        if st.button(suggestion, key=f"f_{suggestion}"):
            run(lambda suggestion=suggestion: client.chat(suggestion))

prompt = st.chat_input(ui.get("placeholder_hint", "Ask a question..."), disabled=bool(questions))
if prompt:
    run(lambda: client.chat(prompt))
