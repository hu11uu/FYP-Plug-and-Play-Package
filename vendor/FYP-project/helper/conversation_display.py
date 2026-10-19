import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
import re
from helper.response import *
import datetime
from agentmemory import (
    create_memory
)
from helper.survey import *
from helper.survey_gen import *
import string
import random

def generate_chat_timestamp():
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    return timestamp

def generate_random_code(length=8):
    characters = string.ascii_letters + string.digits
    random_code = ''.join(random.choice(characters) for _ in range(length))
    return random_code

def append_chat(role, msg):

    current_datetime = generate_chat_timestamp()

    metadata = {
        'username': st.session_state.get('username', 'Anonymous'),
        'conversation': st.session_state.conversation_id,
        'conversation_title': st.session_state.conversation_title,
        'datetime': current_datetime
    }

    new_memory = {
        'role': role,
        'document': msg,
        'metadata': metadata
    }

    if role == 'human':
        st.session_state.cached_human_memories.insert(0, new_memory)
        st.session_state.conversation.append(HumanMessage(content=msg))
        create_memory("human_memories", msg, metadata=metadata)
        
    elif role == "ai":
        st.session_state.cached_ai_memories.insert(0, new_memory)
        st.session_state.conversation.append(AIMessage(content=msg))
        create_memory("ai_memories", msg, metadata=metadata)
    
    st.session_state.messages.append({"role": role, "content": msg})


def handle_follow_up_click(suggestion):
    """
    Called when a follow-up suggestion button is clicked.
    Clears suggestions and sets the clicked suggestion as the next query.
    """
    st.session_state.follow_up_suggestions = []
    st.session_state.pending_follow_up = suggestion


def _safe_answer(query):
    """
    Fix #6 — shared wrapper around answer() used at every call site.

    Previously none of the 3 call sites (chat input, follow-up click,
    scenario click) had error handling. If answer() raised anywhere inside
    its pipeline (an OpenAI timeout, an Azure Search hiccup, a malformed LLM
    JSON response that wasn't already caught internally), the exception
    propagated up, the Streamlit run crashed, and — because is_processing
    had already been set to True before the call — the chat input stayed
    disabled on the next rerun with no way for the student to recover short
    of starting a new conversation.

    This wrapper catches any such failure, resets is_processing itself so
    the caller doesn't have to remember to, and returns a plain-language
    error message instead of letting the run crash.
    """
    try:
        return answer(query), None
    except Exception as e:
        print(f"[Response] answer() failed: {e}")
        st.session_state.is_processing = False
        return None, (
            "Sorry, something went wrong while processing that — please try again. "
            "If this keeps happening, try rephrasing your question."
        )


def main_chatbot_interface():
    display_chat_history()

    if st.session_state.get('survey_shown', False):
        show_survey()
    else:
        st.title("NTU Chatbot Helper")
        st.markdown(f"Ask me anything about **_{st.session_state.chatbot_context}_**.")

        # Initialise follow_up_suggestions if not set
        if 'follow_up_suggestions' not in st.session_state:
            st.session_state.follow_up_suggestions = []
        if 'pending_follow_up' not in st.session_state:
            st.session_state.pending_follow_up = None
        if 'pending_input' not in st.session_state:
            st.session_state.pending_input = None
        if 'msg_count' not in st.session_state:
            st.session_state.msg_count = 0
        if 'is_processing' not in st.session_state:
            st.session_state.is_processing = False
        if 'pending_scenarios' not in st.session_state:
            st.session_state.pending_scenarios = []
        if 'pending_scenario_query' not in st.session_state:
            st.session_state.pending_scenario_query = None
        if 'pending_scenario_intent' not in st.session_state:
            st.session_state.pending_scenario_intent = None
        if 'pending_scenario_answer_query' not in st.session_state:
            st.session_state.pending_scenario_answer_query = None
        if 'selected_scenario' not in st.session_state:
            st.session_state.selected_scenario = None
        if 'active_scenario' not in st.session_state:
            st.session_state.active_scenario = None

        # Display existing messages
        for msg in st.session_state.messages:
            if msg['role'] == 'human':
                with st.chat_message("user"):
                    st.markdown(msg['content'])
            elif msg['role'] == 'ai':
                with st.chat_message("ai"):
                    st.markdown(msg['content'])
                    if "https://www.youtube.com/watch?v=" in msg['content']:
                        youtube_link = re.search(r'https://www\.youtube\.com/watch\?v=[\w-]+', msg['content'])
                        if youtube_link:
                            VIDEO_DATA = youtube_link.group(0)
                            _, container, _ = st.columns([1, 7, 14])
                            container.video(VIDEO_DATA)

        # Survey check
        survey_pending = ('survey_questions' in st.session_state 
                          and len(st.session_state.survey_questions) > 0
                          and len(st.session_state.survey_responses) < len(st.session_state.survey_questions))

        if survey_pending:
            st.info("Please complete the survey to continue chatting.")
            if st.button("📝 Take survey"):
                st.session_state.survey_shown = True
                st.rerun()
            st.chat_input("Enter your question here", disabled=True)

        else:
            is_busy = st.session_state.get("is_processing", False)

            # ── FEATURE 4: Show scenario buttons if query was ambiguous ──
            # Fix (found via testing, Day 4) — same bug class as the main
            # chat input: previously, clicking a scenario button called
            # _safe_answer() INLINE within that same script run, while the
            # OTHER scenario buttons had already been drawn to the browser
            # moments earlier in that same run (Streamlit sends widget
            # deltas as it executes, not batched at the end). That left the
            # other buttons genuinely clickable while the first one's
            # answer was still generating — clicking one mid-generation
            # cancels the in-flight run (Streamlit's standard behaviour on
            # widget interaction), corrupting state and producing "no
            # response" for the original click.
            #
            # Fix: use the exact same safe pattern already proven for the
            # follow-up suggestion buttons below — a click only captures
            # the selection and reruns immediately, WITHOUT calling
            # answer() in that same run. The actual answer generation
            # happens in a separate block on the NEXT run, by which point
            # this button section is skipped entirely (pending_scenarios
            # is already empty), so there's never a run where stale,
            # clickable buttons coexist with an in-flight blocking call.
            # `disabled=is_busy` is added as a second, defense-in-depth
            # layer on top of that.
            # Fix (Day 4, found via testing — the "unclickable buttons" fix
            # above did NOT fully close this). Even with disabled=is_busy,
            # a race remained: the run triggered by clicking button #1
            # skips this whole block entirely (pending_scenarios is already
            # cleared), so it never explicitly tells the browser to remove
            # the old buttons — Streamlit's cleanup for widgets that simply
            # stop being drawn can lag until that next run fully completes.
            # While the blocking answer() call is in progress, the STALE
            # buttons can still be visible and clickable in the browser.
            # Clicking one sends a click for a selection that no longer
            # maps to anything (pending_scenarios is already []), cancels
            # the in-flight run, and — since that run never reached the
            # line resetting is_processing — leaves it stuck True with
            # nothing pending to process. That's the "no response, stopped
            # generating" dead state.
            #
            # Fix: render inside an st.empty() placeholder and call
            # .empty() on it the INSTANT a selection is made, before
            # rerun(). This sends an explicit, immediate "remove this" 
            # instruction as part of the current run's deltas, instead of
            # relying on the next run's implicit "didn't redraw it" cleanup.
            scenario_placeholder = st.empty()
            scenario_input_placeholder = st.empty()
            if st.session_state.pending_scenarios:
                with scenario_placeholder.container():
                    st.markdown("🤔 **To give you the best answer, which situation are you in?**")
                    for i, scenario in enumerate(st.session_state.pending_scenarios):
                        if st.button(
                            f"📌 {scenario}",
                            key=f"scenario_{st.session_state.msg_count}_{i}",
                            disabled=is_busy,
                        ):
                            if not is_busy:
                                selected = scenario
                                query = st.session_state.pending_scenario_query

                                # Clear scenarios immediately so this section is
                                # skipped on the next run, before any blocking
                                # call happens.
                                st.session_state.pending_scenarios = []
                                st.session_state.pending_scenario_query = None
                                st.session_state.pending_scenario_intent = None

                                # Store selected scenario — response.py's answer()
                                # reads this directly to route to
                                # answer_with_scenario() internally.
                                st.session_state.selected_scenario = selected
                                st.session_state.pending_scenario_answer_query = query
                                st.session_state.is_processing = True
                                st.session_state.msg_count += 1

                                # Explicit, immediate removal — clears the
                                # buttons right now, in this run, instead of
                                # waiting for the next run to implicitly
                                # notice they're gone.
                                scenario_placeholder.empty()

                                # NOTE — a matching "push disabled input"
                                # call used to live here too (Day 4, round
                                # 2). Reverted (round 4): using a second key
                                # to dodge the duplicate-ID crash made
                                # Streamlit treat it as a SEPARATE widget
                                # instead of replacing the first, producing
                                # two visibly stacked input bars. Simpler
                                # and correct: just let the input fall back
                                # to whatever the next natural render shows;
                                # self-heal covers the rare case where a
                                # click slips through in the meantime.
                                st.rerun()

                # Normal render path — reached whenever no click has
                # happened yet this run.
                # Fix #7 — previously this call's return value was never
                # captured, so anything the student typed here was rendered
                # and then silently discarded; the clarification screen only
                # ever responded to the scenario buttons above it, never to
                # a typed answer.
                typed_query = scenario_input_placeholder.chat_input(
                    "Or type your own question here", disabled=is_busy,
                )
                if typed_query and not is_busy:
                    # Treat exactly like the normal chat input path below:
                    # clear the scenario prompt, queue this as a fresh
                    # question, and let the next run process it.
                    st.session_state.pending_scenarios = []
                    st.session_state.pending_scenario_query = None
                    st.session_state.pending_scenario_intent = None
                    st.session_state.follow_up_suggestions = []
                    st.session_state.active_scenario = None
                    st.session_state.msg_count += 1
                    st.session_state.is_processing = True
                    st.session_state.pending_input = typed_query

                    # Same immediate-removal pattern used for the scenario
                    # buttons above — clear this run's deltas now rather
                    # than waiting for the next run's implicit cleanup.
                    scenario_placeholder.empty()
                    scenario_input_placeholder.empty()
                    st.rerun()

            # ── Process a scenario selection captured on a previous run ──
            elif st.session_state.get("pending_scenario_answer_query"):
                query = st.session_state.pending_scenario_answer_query
                st.session_state.pending_scenario_answer_query = None

                with st.chat_message('ai'):
                    # Fix #6 — wrapped in _safe_answer instead of a bare call
                    ai_response, error_message = _safe_answer(query)
                    if error_message:
                        st.markdown(error_message)
                        # Patch (found via testing) — the error message
                        # was only ever drawn for this single script run.
                        # st.rerun() immediately after re-executes the
                        # whole page and redraws purely from
                        # st.session_state.messages, so an error message
                        # that was never saved there would silently
                        # vanish on the very next rerun. Persist it the
                        # same way a real answer is persisted.
                        append_chat("ai", error_message)
                    elif ai_response:
                        st.markdown(ai_response)
                        append_chat("ai", ai_response)

                st.session_state.is_processing = False
                st.rerun()

            else:
                # Fix (Day 4) — safety net for the race condition fixed
                # above. If a stale click ever does slip through and
                # cancels an in-flight run before it resets is_processing,
                # the app can land here with is_processing stuck True but
                # genuinely nothing left pending to process — a permanent
                # dead state otherwise (disabled input, no message, no
                # rerun ever triggered again). Since reaching this branch
                # with is_processing=True and no pending_input/
                # pending_follow_up means there is nothing actually in
                # flight, it's safe to self-heal here rather than requiring
                # a full app restart to recover.
                if (
                    st.session_state.get("is_processing", False)
                    and not st.session_state.get("pending_input")
                    and not st.session_state.get("pending_follow_up")
                ):
                    print("[UI] Self-heal — is_processing was stuck True with nothing pending, resetting")
                    st.session_state.is_processing = False

                # ── Show follow-up suggestion buttons only when idle ──
                # Fix (found via testing, Day 4, round 5) — same lagging-
                # cleanup pattern as the scenario buttons and chat input:
                # these buttons were rendered enabled in an earlier, idle
                # run; the moment the user submits something (clicking a
                # button here, or typing a fresh question below), the NEXT
                # run enters a blocking generation phase and this whole
                # `if follow_up_suggestions and not is_busy` block gets
                # skipped outright — meaning nothing ever tells the browser
                # to remove the now-stale buttons, so they can stay visibly
                # present (Streamlit's cleanup for content that stops being
                # drawn can lag until the run fully completes). Wrapped in
                # its own placeholder and explicitly cleared at both points
                # that transition into a busy state, instead of leaving it
                # to be noticed eventually.
                is_busy = (
                    st.session_state.get("is_processing", False) or
                    bool(st.session_state.get("pending_follow_up")) or
                    bool(st.session_state.get("pending_input"))
                )
                followup_placeholder = st.empty()
                if st.session_state.follow_up_suggestions and not is_busy:
                    with followup_placeholder.container():
                        st.markdown("💡 **Want to explore further?**")
                        for i, suggestion in enumerate(st.session_state.follow_up_suggestions):
                            if st.button(
                                suggestion,
                                key=f"followup_{st.session_state.msg_count}_{i}",
                                disabled=is_busy,
                            ):
                                if not st.session_state.get("is_processing", False):
                                    st.session_state.follow_up_suggestions = []
                                    st.session_state.is_processing = True
                                    st.session_state.msg_count += 1
                                    st.session_state.pending_follow_up = suggestion
                                    followup_placeholder.empty()
                                    st.rerun()

                # ── Handle pending follow-up from button click ──
                if st.session_state.pending_follow_up:
                    user_input = st.session_state.pending_follow_up
                    st.session_state.pending_follow_up = None

                    with st.chat_message("user"):
                        st.markdown(user_input)
                    append_chat("human", user_input)

                    with st.chat_message('ai'):
                        # Fix #6 — wrapped in _safe_answer instead of a bare call
                        ai_response, error_message = _safe_answer(user_input)
                        if error_message:
                            st.markdown(error_message)
                            # Patch (found via testing) — persist the error the
                            # same way a real answer is persisted, otherwise it
                            # silently disappears on the next st.rerun() since
                            # the chat redraws purely from session_state.messages.
                            append_chat("ai", error_message)
                        elif ai_response is not None:
                            st.markdown(ai_response)
                            append_chat("ai", ai_response)
                        else:
                            st.markdown("Please select your situation above to get a tailored answer.")

                    st.session_state.is_processing = False

                    if 'survey_questions' in st.session_state and len(st.session_state.survey_questions) > 0:
                        st.rerun()
                    st.rerun()

                # ── Handle normal chat input ──
                # Fix (found via testing, Day 4) — the input had no
                # `disabled=` tied to is_processing at all, so a student
                # could type and submit a new message while a previous
                # response was still generating, silently interrupting it.
                # Tying this to is_busy closes that gap directly.
                #
                # NOTE — a "push a second disabled render immediately"
                # variant was tried and reverted (Day 4, rounds 3-4): using
                # two different keys to dodge a duplicate-ID crash caused
                # Streamlit to treat them as two SEPARATE persistent
                # widgets instead of one replacing the other, producing two
                # visibly stacked input bars — a worse, more visible bug
                # than the narrow timing gap it was meant to close. Kept
                # simple here: one call, disabled=is_busy. The self-heal
                # check above (in the fallthrough else branch) already
                # handles the rare case where a click slips through during
                # the small remaining window, so the app recovers instead
                # of freezing — that's the actual safety net, not trying to
                # force a zero-width disable window.
                user_input = st.chat_input("Enter your question here", disabled=is_busy)
                if user_input:
                    st.session_state.follow_up_suggestions = []
                    st.session_state.msg_count += 1
                    st.session_state.is_processing = True
                    st.session_state.active_scenario = None  # Clear scenario context on new question
                    st.session_state.pending_input = user_input
                    st.rerun()

                # Process pending input after suggestions are cleared
                if st.session_state.get("pending_input"):
                    user_input = st.session_state.pending_input
                    st.session_state.pending_input = None

                    with st.chat_message("user"):
                        st.markdown(user_input)
                    append_chat("human", user_input)

                    with st.chat_message('ai'):
                        # Fix #6 — wrapped in _safe_answer instead of a bare call
                        ai_response, error_message = _safe_answer(user_input)
                        if error_message:
                            st.markdown(error_message)
                            # Patch (found via testing) — persist the error the
                            # same way a real answer is persisted, otherwise it
                            # silently disappears on the next st.rerun() since
                            # the chat redraws purely from session_state.messages.
                            append_chat("ai", error_message)
                        elif ai_response is not None:
                            st.markdown(ai_response)
                            append_chat("ai", ai_response)
                        else:
                            st.markdown("Please select your situation above to get a tailored answer.")

                    st.session_state.is_processing = False

                    if 'survey_questions' in st.session_state and len(st.session_state.survey_questions) > 0:
                        st.rerun()
                    st.rerun()

        
def display_chat_history():

    today = datetime.date.today()
    one_day_ago = today - datetime.timedelta(days=1)
    seven_days_ago = today - datetime.timedelta(days=7)
    thirty_days_ago = today - datetime.timedelta(days=30)

    with st.sidebar:
        st.markdown(f"# {st.session_state.username}'s Chat History")

        if st.button("New Conversation", key="new_conversation_button"):
            st.session_state.conversation = []
            st.session_state.messages = []
            st.session_state.conversation_id = generate_random_code(8)
            st.session_state.new_conversation = True
            st.session_state.conversation_title = None
            st.session_state.follow_up_suggestions = []
            st.session_state.pending_scenarios = []
            st.session_state.selected_scenario = None
            st.session_state.active_scenario = None

        conversations = get_conversation_ids()

        def get_date_from_datetime(datetime_str):
            return datetime.datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S').date()

        st.markdown("#### Today")
        today_conversations = [conv for conv in conversations if get_date_from_datetime(conv['datetime']) == today]
        for i, conv in enumerate(today_conversations):
            if st.button(f"Chat {i + 1}: {conv['conversation_id']} {conv['conversation_title']}", key=f"today_{conv['conversation_id']}"):
                get_combined_memories(conv['conversation_id'])
                st.session_state.conversation_id = conv['conversation_id']
                st.session_state.conversation_title = conv['conversation_title']
                st.session_state.new_conversation = False
                st.session_state.follow_up_suggestions = []

        st.markdown("#### Yesterday")
        yesterday_conversations = [conv for conv in conversations if get_date_from_datetime(conv['datetime']) == one_day_ago]
        for i, conv in enumerate(yesterday_conversations):
            if st.button(f"Chat {i + 1}: {conv['conversation_id']} {conv['conversation_title']}", key=f"yesterday_{conv['conversation_id']}"):
                get_combined_memories(conv['conversation_id'])
                st.session_state.conversation_id = conv['conversation_id']
                st.session_state.conversation_title = conv['conversation_title']
                st.session_state.new_conversation = False
                st.session_state.follow_up_suggestions = []

        st.markdown("#### Previous 7 Days")
        last_seven_days_conversations = [conv for conv in conversations if seven_days_ago <= get_date_from_datetime(conv['datetime']) < one_day_ago]
        for i, conv in enumerate(last_seven_days_conversations):
            if st.button(f"Chat {i + 1}: {conv['conversation_id']} {conv['conversation_title']}", key=f"last7days_{conv['conversation_id']}"):
                get_combined_memories(conv['conversation_id'])
                st.session_state.conversation_id = conv['conversation_id']
                st.session_state.conversation_title = conv['conversation_title']
                st.session_state.new_conversation = False
                st.session_state.follow_up_suggestions = []

        st.markdown("#### Previous 30 Days")
        last_thirty_days_conversations = [conv for conv in conversations if thirty_days_ago <= get_date_from_datetime(conv['datetime']) < seven_days_ago]
        for i, conv in enumerate(last_thirty_days_conversations):
            if st.button(f"Chat {i + 1}: {conv['conversation_id']} {conv['conversation_title']}", key=f"last30days_{conv['conversation_id']}"):
                get_combined_memories(conv['conversation_id'])
                st.session_state.conversation_id = conv['conversation_id']
                st.session_state.conversation_title = conv['conversation_title']
                st.session_state.new_conversation = False
                st.session_state.follow_up_suggestions = []

def get_conversation_ids():
    ai_memories = st.session_state.cached_ai_memories
    conversations = {}
    
    for ai in ai_memories:
        username = ai.get('metadata', {}).get('username')
        if username != st.session_state.username:
            continue
        conversation_id = ai.get('metadata', {}).get('conversation')
        datetime_str = ai.get('metadata', {}).get('datetime')
        conversation_title = ai.get('metadata', {}).get('conversation_title')
        
        if conversation_id:
            current_datetime = datetime.datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
            
            if conversation_id not in conversations:
                conversations[conversation_id] = {
                    'conversation_id': conversation_id,
                    'datetime': datetime_str,
                    'conversation_title': conversation_title,
                    'username': st.session_state.username
                }
            else:
                existing_datetime = datetime.datetime.strptime(
                    conversations[conversation_id]['datetime'],
                    '%Y-%m-%d %H:%M:%S'
                )
                if current_datetime < existing_datetime:
                    conversations[conversation_id]['datetime'] = datetime_str

                if not conversations[conversation_id].get('conversation_title') and conversation_title:
                    conversations[conversation_id]['conversation_title'] = conversation_title
    
    return list(conversations.values())

def get_combined_memories(conversation_id):
    st.session_state.conversation = []
    st.session_state.messages = []

    human_memories = sorted(
        [human for human in st.session_state.cached_human_memories 
         if human.get('metadata', {}).get('conversation') == conversation_id
         and human.get('metadata', {}).get('username') == st.session_state.username],
        key=lambda x: x['metadata']['datetime']
    )
    
    ai_memories = sorted(
        [ai for ai in st.session_state.cached_ai_memories 
         if ai.get('metadata', {}).get('conversation') == conversation_id
         and ai.get('metadata', {}).get('username') == st.session_state.username],
        key=lambda x: x['metadata']['datetime']
    )

    for human, ai in zip(human_memories, ai_memories):
        st.session_state.conversation.append(HumanMessage(content=human['document']))
        st.session_state.messages.append({"role": "human", "content": human['document']})
        
        st.session_state.conversation.append(AIMessage(content=ai['document']))
        st.session_state.messages.append({"role": "ai", "content": ai['document']})