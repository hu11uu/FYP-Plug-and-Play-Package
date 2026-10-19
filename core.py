"""SurveyChatbot, the shared core of the plug-and-play package.

Each host builds one SurveyChatbot per subject and calls new_session(),
chat(), submit_survey() and snapshot(). The class prepares each session's
state and calls the original response.answer() and survey.complete_survey().
It makes no survey decisions itself. Those all come from the unchanged files
in vendor/FYP-project/.
"""
import contextlib
import io
import secrets
import threading
from pathlib import Path

from bridge import State, Search, Store, HumanMessage, AIMessage, bind, install

install()
from helper import response, survey  # noqa: E402  (original modules, unchanged)
from providers import OfflineProvider, AzureProvider

LOCK = threading.RLock()  # One call into the original code at a time (shared print capture and cache).


class SurveyChatbot:
    """Runs the original algorithm for one subject, with one state per session.

    Parameters
    ----------
    content_pack : list[dict]
        The subject's topics, each with id, title, keywords and content, as in
        deployments/<name>/content/course.json.
    survey_bank : list[str]
        The feedback questions. The same 34 are shared by every subject.
    chatbot_context : str
        Short scope description. The original domain filter
        (is_relevant_to_context() in helper/response.py) reads it to decide
        what is in scope, and the greeting takes the course name from it.
    conversation_title : str
        Title shown before the algorithm sets its own.
    database : str
        Path to the SQLite file for saved surveys. Its folder is created if
        missing.
    mode : "offline" | "azure"
        "offline" uses the scripted test model. "azure" checks the four
        FYP_AZURE_* variables straight away.
    """

    def __init__(self, content_pack, survey_bank, chatbot_context,
                 conversation_title, database, mode="offline"):
        self.content_pack = content_pack
        self.survey_bank = survey_bank
        self.chatbot_context = chatbot_context
        self.conversation_title = conversation_title
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(database)
        self.mode = mode
        self.sessions = {}
        if mode == "azure":
            AzureProvider()  # Check the Azure settings now, not on the first message.

    def new_session(self):
        with LOCK:
            token = secrets.token_urlsafe(32)
            search = Search(self.content_pack)
            state = State(username=token, messages=[], conversation=[], msg_count=0,
                          new_conversation=True, conversation_title=self.conversation_title,
                          chatbot_context=self.chatbot_context,
                          survey_questions=[], survey_responses=[], survey_shown=False,
                          surveys_shown_count=0, active_scenario=None, pending_scenarios=[],
                          student_struggling=False, follow_up_suggestions=[],
                          information_search=search, notes_search=search,
                          surveyquestions_search=Search([{"content": q} for q in self.survey_bank], True),
                          sql_client=self.store, events=[], last_error=None)
            state.openai_llm = AzureProvider() if self.mode == "azure" else OfflineProvider(search)
            self.sessions[token] = state
            return token

    def snapshot(self, token):
        state = self.sessions[token]
        keys = ("messages", "msg_count", "survey_questions", "survey_responses", "surveys_shown_count", "pending_scenarios", "follow_up_suggestions", "last_error")
        result = {k: state.get(k) for k in keys}
        result.update(mode=self.mode, events=state.events[-20:], course=self.content_pack,
                      save_gave_up=state.get("_survey_save_gave_up", False))
        return result

    def chat(self, token, query, scenario=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("Enter a question between 1 and 4,000 characters.")
        with LOCK:
            state = self.sessions[token]
            if state.survey_questions: raise ValueError("Complete the pending survey before continuing.")
            if scenario is not None and scenario not in state.pending_scenarios:
                raise ValueError("Select one of the offered scenarios.")
            if scenario:
                query = state.pending_scenario_query
                state.selected_scenario = scenario
                state.pending_scenarios = []
                state.pending_scenario_query = None
                state.pending_scenario_intent = None
            elif state.pending_scenarios:
                state.pending_scenarios = []
                state.pending_scenario_query = None
                state.pending_scenario_intent = None
                state.active_scenario = None
            state.follow_up_suggestions = []
            state.last_error = None
            state.msg_count += 1
            if scenario is None:
                state.messages.append({"role": "human", "content": query})
                state.conversation.append(HumanMessage(query))
            trace = io.StringIO()
            with bind(state), contextlib.redirect_stdout(trace):
                try:
                    answer = response.answer(query)  # The original orchestration, unchanged.
                except Exception:
                    # Equivalent UI boundary to the original _safe_answer: unlock
                    # the host and show an error, without inventing a model answer.
                    answer = "Sorry, something went wrong while processing that — please try again. If this keeps happening, try rephrasing your question."
                    state.last_error = "The model or adapter failed. Please try again."
            if answer is not None:
                state.messages.append({"role": "ai", "content": answer})
                state.conversation.append(AIMessage(answer))
            self._event(state, "chat", trace.getvalue())
            return self.snapshot(token)

    def submit_survey(self, token, ratings):
        with LOCK:
            state = self.sessions[token]
            if not state.survey_questions: raise ValueError("No survey is pending.")
            if not isinstance(ratings, list) or len(ratings) != len(state.survey_questions) or any(type(x) is not int or not 1 <= x <= 5 for x in ratings):
                raise ValueError("Answer every survey item with a rating from 1 to 5.")
            state.survey_responses = list(ratings)
            trace = io.StringIO()
            with bind(state), contextlib.redirect_stdout(trace):
                saved = survey.complete_survey()  # Original retries, clearing, counter.
            self._event(state, "save", trace.getvalue())
            state.last_error = None if saved else ("Original algorithm discarded the responses after its third failed save." if state.get("_survey_save_gave_up") else "Save failed. Responses remain pending; retry the save.")
            result = self.snapshot(token)
            result["saved"] = saved
            return result

    @staticmethod
    def _event(state, kind, log):
        # Show only decision lines, not full prompts or save payloads.
        lines = [line for line in log.splitlines() if line.startswith("[Survey]") and "SURVEY DATA LOST" not in line]
        state.events.append({"turn": state.msg_count, "action": kind, "decisions": lines})
