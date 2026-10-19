import streamlit as st
import time
import datetime
import uuid

# Fix #5 — cap on how many times complete_survey() will retry a failed
# save before giving up and finally clearing state (see complete_survey()).
_MAX_SAVE_ATTEMPTS = 3


def generate_survey_timestamp():
    """Generates current timestamp for survey records."""
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    return timestamp


def get_contextual_intro_message() -> str:
    """
    Returns a contextual intro message based on conversation type.
    """
    conversation_type = st.session_state.get("last_conversation_type", "general")
    last_query = st.session_state.get("last_substantive_query", "").lower()
    intent = st.session_state.get("last_substantive_intent", "other")

    if conversation_type == "teaching":
        if st.session_state.get("student_struggling", False) or intent == "difficulty":
            return "Your feedback helps us improve how difficult topics are explained to future students."
        return f"Quick feedback on your learning experience — your response helps improve how concepts are taught in {st.session_state.get('chatbot_context', 'this course')}."

    if conversation_type == "assessment":
        return "Since you asked about assessment, quick feedback on how the information was communicated would be helpful."

    if conversation_type == "engagement":
        return "Since you shared your learning experience, your feedback helps us understand how the course challenges students."

    # Default — general
    return f"Help us improve {st.session_state.get('chatbot_context', 'this course')} by sharing quick feedback on your learning experience."


def show_survey():
    """
    Display the survey interface to the student.
    Shows one question at a time with a 5-point rating scale.
    """
    survey_container = st.container()

    with survey_container:
        st.markdown("""
            <div class="survey-container">
                <h2>Quick Survey</h2>
            </div>
        """, unsafe_allow_html=True)

        intro_message = get_contextual_intro_message()
        st.markdown(f"*{intro_message}*")

        st.warning("⚠️ Please complete all questions before refreshing or navigating away. Your responses are saved only after the survey is complete.")

        progress = len(st.session_state.survey_responses)
        total = len(st.session_state.survey_questions)

        if total > 0:
            st.progress(progress / total)

            if progress < total:
                full_question = st.session_state.survey_questions[progress]
                display_question = full_question

                parts = full_question.split(': ', 1)
                if len(parts) == 2:
                    prefix = parts[0].strip()
                    if len(prefix) <= 4 and prefix[0].isalpha() and (
                        prefix[1:].isdigit() or
                        (len(prefix) > 2 and prefix[1].isdigit())
                    ):
                        display_question = parts[1]

                st.info(f"Q{progress + 1}: {display_question}")

                ratings = ["Very Poor", "Poor", "Average", "Good", "Very Good"]
                cols = st.columns(5)
                for i, (col, label) in enumerate(zip(cols, ratings), start=1):
                    with col:
                        if st.button(
                            label,
                            type="primary",
                            key=f"survey_btn_{progress}_{i}",
                            use_container_width=True,
                            on_click=on_survey_input_change,
                            args=(i,)
                        ):
                            pass

            else:
                # Fix #6 — save happens BEFORE we tell the student it worked.
                # complete_survey() now returns whether the save actually
                # succeeded, so a failed save no longer shows a false
                # "Thank you!" — the student sees an honest message either way.
                save_succeeded = complete_survey()
                gave_up = st.session_state.pop('_survey_save_gave_up', False)

                if save_succeeded:
                    st.success("Returning to chat...")
                    st.toast("Thank you for your feedback!", icon="👍")
                elif gave_up:
                    # Fix #5 — only shown once complete_survey() has actually
                    # exhausted its retries and cleared state; this is the
                    # one case where the data really is gone, so the message
                    # says so honestly instead of claiming it's retained.
                    st.warning(
                        "We couldn't save your responses after several attempts. "
                        "Your feedback for this survey was not saved — please let "
                        "your instructor know if this keeps happening."
                    )
                    print("[Survey] ALERT: survey save gave up — data was not persisted to MongoDB")
                else:
                    # Fix #5 — state was deliberately left intact by
                    # complete_survey() (survey_shown stays True), so this is
                    # genuinely still true: nothing has been cleared yet, and
                    # the next render will retry the same save automatically.
                    st.warning(
                        "We couldn't save your responses due to a connection issue. "
                        "Your feedback wasn't lost from this session — retrying automatically."
                    )
                    print("[Survey] ALERT: survey save failed — will retry")

                survey_container.empty()
                time.sleep(3)
                st.rerun()


def on_survey_input_change(rating):
    """Record student's rating response."""
    if len(st.session_state.survey_responses) < len(st.session_state.survey_questions):
        st.session_state.survey_responses.append(rating)


def complete_survey() -> bool:
    """
    Save completed survey to MongoDB and reset survey state.

    Fix #6 (kept) — save failures are logged with a clearly-flagged ALERT
    line, and the return value tells the caller (show_survey) whether the
    save actually succeeded so it can be honest with the student.

    Fix #5 — previously this ALWAYS cleared st.session_state['survey_
    questions']/['survey_responses'] regardless of save_succeeded, while
    show_survey() told the student on failure "your feedback wasn't lost
    from this session" — which was false: it was wiped from session state
    in the very same call, right after that message was shown, with no
    persisted copy either. Now:
      1. The write uses an idempotent upsert keyed by a stable per-attempt
         survey_id (generated once and reused across retries), so retrying
         after a failure can never create a duplicate record even if a
         prior attempt actually succeeded server-side but the client never
         got confirmation (e.g. a timeout after a successful write).
      2. Survey state (questions/responses/survey_id) is only cleared once
         save_succeeded is actually True. On failure, state is left intact
         so the natural re-render/retry in show_survey() tries again with
         the SAME data and SAME survey_id, up to _MAX_SAVE_ATTEMPTS times.
      3. Only after exhausting retries does this give up and clear state —
         and it reports that outcome distinctly (see the 3rd return value
         consumed by show_survey()) so the "wasn't lost" message is never
         shown for a save that was, in fact, abandoned.
    """
    save_succeeded = False
    gave_up = False

    if 'survey_questions' in st.session_state and 'survey_responses' in st.session_state:
        # Stable ID for this survey attempt — created once, reused on every
        # retry, so re-tries upsert the same document instead of inserting
        # a fresh one each time.
        if not st.session_state.get('_current_survey_id'):
            st.session_state['_current_survey_id'] = str(uuid.uuid4())
        survey_id = st.session_state['_current_survey_id']

        survey_data = {
            "_id": survey_id,
            "user": st.session_state.get("username", "Anonymous"),
            "questions": st.session_state.survey_questions,
            "answers": st.session_state.survey_responses,
            "timestamp": generate_survey_timestamp(),
            "intent_context": st.session_state.get("detected_intent_for_survey", "other"),
            "last_query": st.session_state.get("last_substantive_query", "")
        }

        try:
            from pymongo import MongoClient
            from dotenv import load_dotenv
            load_dotenv()
            import os
            fresh_client = MongoClient(os.environ['PYMONGO_CONNECTION_STRING'])
            surveys_collection = fresh_client['chatbot']['surveys']
            surveys_collection.update_one(
                {"_id": survey_id}, {"$set": survey_data}, upsert=True
            )
            fresh_client.close()
            print(f"[Survey] Saved survey {survey_id} with {len(st.session_state.survey_questions)} questions")
            save_succeeded = True
        except Exception as e:
            print(f"[Survey] ALERT: primary save failed for {survey_id} — {e}")
            try:
                sql_client = st.session_state.sql_client
                chatbot_db = sql_client['chatbot']
                surveys_collection = chatbot_db['surveys']
                surveys_collection.update_one(
                    {"_id": survey_id}, {"$set": survey_data}, upsert=True
                )
                print(f"[Survey] Saved survey (fallback) {survey_id} with {len(st.session_state.survey_questions)} questions")
                save_succeeded = True
            except Exception as e2:
                attempts = st.session_state.get('_survey_save_attempts', 0) + 1
                st.session_state['_survey_save_attempts'] = attempts
                print(f"[Survey] ALERT: fallback save ALSO failed (attempt {attempts}/{_MAX_SAVE_ATTEMPTS}) for {survey_id} — {e2}")
                if attempts >= _MAX_SAVE_ATTEMPTS:
                    print(f"[Survey] ALERT: giving up after {attempts} attempts — SURVEY DATA LOST: {survey_data}")
                    gave_up = True
                save_succeeded = False

    if save_succeeded or gave_up:
        st.session_state.survey_shown = False
        st.session_state['survey_questions'] = []
        st.session_state['survey_responses'] = []
        st.session_state.pop('detected_intent_for_survey', None)
        st.session_state.pop('_current_survey_id', None)
        st.session_state.pop('_survey_save_attempts', None)

        # Reset struggling flag
        st.session_state['student_struggling'] = False
        print("[Survey] Struggling flag reset after survey completion")
    else:
        # Save failed but retries remain — keep survey_shown True and the
        # state intact so show_survey() re-renders this same completed
        # screen and complete_survey() gets called again with the same
        # data and same survey_id on the next run.
        st.session_state.survey_shown = True

    # Increment session survey counter — only on successful save, so a
    # failed/abandoned save doesn't silently consume one of the student's
    # 2 allowed surveys for the session.
    if save_succeeded:
        current_count = st.session_state.get("surveys_shown_count", 0)
        st.session_state["surveys_shown_count"] = current_count + 1
        print(f"[Survey] Total surveys completed this session: {current_count + 1}")

    st.session_state['_survey_save_gave_up'] = gave_up
    return save_succeeded