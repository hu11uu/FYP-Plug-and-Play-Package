import json
import streamlit as st
from helper import llm_parallel

try:
    # Fix #3 — restores semantic/vector search capability (previously dropped
    # in this version relative to the original hybrid_search implementation).
    from azure.search.documents.models import VectorizableTextQuery
    _VECTOR_SEARCH_AVAILABLE = True
except ImportError:
    _VECTOR_SEARCH_AVAILABLE = False

from helper.signals import (
    SATISFACTION_PHRASES,
    QUESTION_INDICATORS,
    ASSESSMENT_KEYWORDS,
    QUESTION_CATEGORIES,
    get_question_category,
    is_struggle_signal,
    is_dissatisfaction_signal,
    is_personal_experience_signal,
    contains_any,
)

# ─────────────────────────────────────────────────────────────────────────────
# INTELLIGENT SURVEY TRIGGERING SYSTEM — v3.4
# Feature 5 — Direct Conversation-Aware Survey Question Selection
#
# Three-Tier Hierarchical Survey Triggering System:
#
# Tier 1 — Satisfaction Signal
# Tier 2 — Confidence-Based Trigger
# Tier 3 — Contextual Fallback
#
# v3.4 Changes:
# Fix #7 — All struggle/dissatisfaction/satisfaction/personal-experience
#          keyword matching now goes through helper/signals.py instead of
#          hand-typed, drifting local copies.
# Fix #4 — Removed the duplicate "increased my interest in the course"
#          question from the "core" category (it was already listed under
#          "teaching", which meant get_question_category() always resolved
#          it to "teaching" and the "core" copy was silently unreachable).
# Fix #5 — Added a minimum-gap cooldown between survey triggers, on top of
#          the existing per-session cap, so two surveys can no longer fire
#          on back-to-back messages.
# Fix #3 — hybrid_search() now performs true hybrid (keyword + vector)
#          search when the search index's embedding field is available,
#          instead of keyword-only search.
# ─────────────────────────────────────────────────────────────────────────────

# ── CONFIDENCE THRESHOLD — configurable for evaluation tuning ─────────────────
TIER2_CONFIDENCE_THRESHOLD = 80

# ── SESSION-END LIKELIHOOD THRESHOLD — Fix #1 (Tier 2 redesign) ──────────────
# Tier 2 previously fired whenever a single exchange felt "resolved," which
# could happen on message 2 just as easily as message 8 — not actually
# targeting "near the end of the session" the way the project's stated goal
# requires. This threshold gates a second, independent signal: does the
# conversation's pacing look like the student is wrapping up, rather than
# continuing to explore. Both signals must agree before Tier 2 fires.
#
# RECALIBRATED (Leftover #2, data-informed, not a blind guess) — live testing
# across 5 real multi-turn exchanges produced session-end scores of
# 25%, 40%, 45%, 38%, 25% — never once reaching the original threshold of 60.
# The threshold has been lowered to 45, matching the actual observed ceiling
# from real data, rather than sitting arbitrarily above anything the model
# has ever output.
#
# IMPORTANT — this does NOT loosen the model's own judgment. The firing
# condition is `likely_wrapping_up AND end_likelihood >= threshold` — both
# must be true. This value only removes an extra numeric ceiling that was
# set higher than the model's real observed range; it does not override or
# relax the model's own boolean call on whether the session looks like it's
# ending. If the boolean was already False in the cases tested, this change
# has zero effect on those cases — it only unblocks situations where the
# model's own judgment already said "yes, wrapping up" but got vetoed
# purely by an arbitrary number above its real observed range.
#
# CAVEAT — this is a recalibration based on only 5 observed data points, not
# a final, fully-validated answer. Revisit with real usage-volume data once
# available; don't treat 45 as sacred, treat it as "closer to reality than
# 60 was, given everything observed so far."
SESSION_END_LIKELIHOOD_THRESHOLD = 45

# ── COOLDOWN — configurable, tune based on real conversation-length testing ──
# Minimum number of messages that must pass between one survey firing and the
# next being allowed to fire, in addition to the existing 2-per-session cap.
# Starting value per discussion: roughly "let ~2 more questions happen first".
SURVEY_COOLDOWN_MESSAGES = 4

# ── QUESTION CATEGORY MAPPING ─────────────────────────────────────────────────
# Leftover item 2 — QUESTION_CATEGORIES and the baseline get_question_category()
# now live in helper/signals.py as the single shared source (imported above),
# instead of being duplicated here and in survey_visualisation_page.py, which
# is what let the two copies silently drift apart and caused the Fix #4
# collision bug in the first place.
#
# What follows here is the SMART fallback layer on top of that baseline —
# get_question_category_resolved() — which handles questions that aren't in
# the hardcoded dict at all (e.g. new questions added via the Survey
# Generator page), instead of silently defaulting them to "general" forever.
# It checks, in order: (1) the fast hardcoded dict, (2) a Mongo cache of
# previously-classified questions, (3) as a last resort, asks the LLM to
# classify it once and saves that result to Mongo so every future lookup
# is instant and free. This needed no Azure Search changes and no Portal
# access — it only uses Mongo, which the app already has full access to.

_CATEGORY_COLLECTION_NAME = "question_categories"
_VALID_CATEGORIES = set(QUESTION_CATEGORIES.keys())  # {"teaching", "assessment", "engagement", "core"}


def _get_category_mongo_collection():
    """
    Shared helper to reach the question_categories collection, reusing the
    same session-state Mongo client pattern already used elsewhere in this
    file (run_survey_pipeline's answered-questions lookup), rather than
    opening a fresh connection for every category lookup.
    """
    try:
        sql_client = st.session_state.sql_client
        return sql_client['chatbot'][_CATEGORY_COLLECTION_NAME]
    except Exception as e:
        print(f"[Category] Could not reach Mongo for category lookup: {e}")
        return None


def _get_cached_category_from_mongo(question_text: str) -> str | None:
    """Read-only lookup — does not classify, just checks if already cached."""
    collection = _get_category_mongo_collection()
    if collection is None:
        return None
    try:
        doc = collection.find_one({"question_text": question_text.strip().lower()})
        if doc and doc.get("category") in _VALID_CATEGORIES:
            return doc["category"]
    except Exception as e:
        print(f"[Category] Mongo cache read failed: {e}")
    return None


def _save_category_to_mongo(question_text: str, category: str):
    """
    Best-effort write — deliberately non-fatal. If this fails, the question
    just gets re-classified by the LLM next time it's encountered instead of
    being cached; it should never block or break the survey pipeline.
    """
    collection = _get_category_mongo_collection()
    if collection is None:
        return
    try:
        collection.update_one(
            {"question_text": question_text.strip().lower()},
            {"$set": {"question_text": question_text.strip().lower(), "category": category}},
            upsert=True,
        )
        print(f"[Category] Cached new classification to Mongo: '{question_text[:50]}...' -> {category}")
    except Exception as e:
        print(f"[Category] Mongo cache write failed (non-fatal, will re-classify next time): {e}")


def _classify_question_via_llm(question_text: str) -> str:
    """
    Last-resort classification for a question not found in the hardcoded
    dict or the Mongo cache — i.e. a genuinely new question. Runs once per
    question ever (the result gets cached), not once per survey trigger.
    """
    if "openai_llm" not in st.session_state:
        return "general"

    classify_prompt = f"""
    You are categorising a student feedback survey question into exactly ONE
    of these four categories, based on what the question is actually asking
    the student to reflect on:

    - "teaching": clarity of explanations, examples, structure, pacing, how
      concepts were taught
    - "assessment": quizzes, tasks, instructions, learning outcomes,
      assessment-related guidance
    - "engagement": personal challenge, creativity, deep thinking, exploring
      different perspectives — things the student can only answer if they
      actually experienced being personally challenged or engaged
    - "core": general chatbot/TA effectiveness, approachability,
      communication clarity, overall usefulness as a learning resource —
      use this if the question doesn't clearly fit the other three

    Question: "{question_text}"

    Respond ONLY with one word: teaching, assessment, engagement, or core.
    """

    try:
        raw = st.session_state.openai_llm.invoke(classify_prompt).content.strip().lower()
        for valid in _VALID_CATEGORIES:
            if valid in raw:
                return valid
        print(f"[Category] LLM classification returned unrecognised value '{raw}' — defaulting to 'core'")
        return "core"
    except Exception as e:
        print(f"[Category] LLM classification failed: {e} — defaulting to 'general'")
        return "general"


def get_question_category_resolved(question_text: str) -> str:
    """
    The smart, fallback-aware version of get_question_category() — use this
    everywhere in the live survey pipeline instead of the plain signals.py
    version, so genuinely new questions get classified automatically instead
    of silently defaulting to "general" forever.

    Resolution order: hardcoded dict -> Mongo cache -> LLM classification
    (which then gets cached for next time).
    """
    static_result = get_question_category(question_text)
    if static_result != "general":
        return static_result

    cached = _get_cached_category_from_mongo(question_text)
    if cached is not None:
        return cached

    print(f"[Category] Question not in hardcoded dict or Mongo cache — classifying via LLM: '{question_text[:60]}...'")
    classified = _classify_question_via_llm(question_text)
    if classified != "general":
        _save_category_to_mongo(question_text, classified)
    return classified


def filter_questions_by_category(questions: list, conversation_type: str, min_required: int = 2) -> list:
    if conversation_type == "general":
        return questions
    filtered = [q for q in questions if get_question_category_resolved(q) == conversation_type]
    count = len(filtered)
    print(f"[Survey] Category filter: {count} {conversation_type} questions available")
    if count >= min_required:
        print(f"[Survey] Pre-filtered to {count} {conversation_type} questions ✅")
        return filtered
    else:
        print(f"[Survey] Not enough {conversation_type} questions ({count}) — using all {len(questions)} available")
        return questions


def hybrid_search(query, top_k=5):
    """
    Fix #3 — restores true hybrid (keyword + vector) search.
    Falls back to keyword-only search if the vector SDK/field isn't
    available, so this degrades gracefully rather than breaking outright.
    """
    try:
        search_kwargs = {
            "search_text": query,
            "top": top_k,
        }

        if _VECTOR_SEARCH_AVAILABLE:
            try:
                vector_query_content = VectorizableTextQuery(
                    text=query,
                    k_nearest_neighbors=50,
                    fields="content_vector"
                )
                search_kwargs["vector_queries"] = [vector_query_content]
            except Exception as ve:
                print(f"[Survey] Vector query construction failed, falling back to keyword-only: {ve}")

        results = st.session_state.surveyquestions_search.search(**search_kwargs)
        content = []
        for result in results:
            if 'content' in result:
                content.append(result['content'])
            elif 'text' in result:
                content.append(result['text'])
            else:
                for key, value in result.items():
                    if isinstance(value, str):
                        content.append(str(value))
                        break
        return content
    except Exception as e:
        # Fix (patch) — same issue as response.py: a vector query can fail at
        # search time if the index's vector field has no vectorizer
        # configured. Retry keyword-only instead of returning nothing.
        print(f"Error performing hybrid search (with vector query): {e}")
        try:
            print("Retrying survey question search without vector query...")
            results = st.session_state.surveyquestions_search.search(search_text=query, top=top_k)
            content = []
            for result in results:
                if 'content' in result:
                    content.append(result['content'])
                elif 'text' in result:
                    content.append(result['text'])
                else:
                    for key, value in result.items():
                        if isinstance(value, str):
                            content.append(str(value))
                            break
            return content
        except Exception as e2:
            print(f"Error performing search (keyword-only retry also failed): {e2}")
            return []


@st.cache_data(ttl=300, show_spinner=False)
def search_all():
    """
    Fix (Leftover #4) — this used to hit Azure Search fresh on every single
    survey trigger, even though the survey question bank rarely changes.
    Now cached for 5 minutes — same pattern already used elsewhere in this
    project (get_survey_data(), generate_ai_summary()).

    Tradeoff, stated plainly: if a professor adds a new question via the
    Survey Generator page, it can take up to 5 minutes to show up here,
    rather than being instantly available. Given how infrequently the
    question bank actually changes compared to how often this function
    gets called (every survey trigger, potentially several times per
    conversation), this is a reasonable tradeoff — a 5-minute TTL is short
    enough that a professor testing right after adding a question would
    very rarely be the one to notice the delay, and it removes an Azure
    Search round-trip from every survey evaluation in the meantime.

    One more honest caveat: if a call happens to fail (a transient Azure
    hiccup), that empty-list result gets cached too, for up to 5 minutes —
    same as any cached function. Given failures here were already
    unretried before this fix (every prior call just failed fresh each
    time with no backoff), this isn't a new risk class, just a slightly
    longer window on an existing one. Not worth the added complexity of a
    separate uncached retry path for how rarely this should actually fire
    against a correctly configured Azure Search instance.
    """
    all_docs = []
    try:
        results = st.session_state.surveyquestions_search.search(
            search_text="*", select=["content"], top=1000,
        )
        for doc in results:
            all_docs.append(doc["content"])
        if all_docs:
            print(f"[Survey] Sample questions from Azure:")
            for q in all_docs[:3]:
                print(f"  → {repr(q)}")
        return all_docs
    except Exception as e:
        print(f"Error performing search: {e}")
        return []


def get_conversation_context(current_response: str = "") -> str:
    """
    Fix #2 — accepts the response generated THIS turn as an optional extra
    argument. st.session_state.messages only gets this turn's AI response
    appended AFTER answer() returns (see conversation_display.py's
    append_chat), so without this, survey question selection was evaluating
    against conversation history that was always one message behind —
    missing exactly the answer the student is about to be asked to rate.
    """
    messages = st.session_state.get("messages", [])
    recent = messages[-6:] if len(messages) >= 6 else messages
    lines = []
    for m in recent:
        role = "Student" if m.get("role") == "human" else "Chatbot"
        content = m.get("content", "").strip()
        if content:
            truncated = content[:200] + "..." if len(content) > 200 else content
            lines.append(f"{role}: {truncated}")

    if current_response and current_response.strip():
        truncated = current_response.strip()
        truncated = truncated[:200] + "..." if len(truncated) > 200 else truncated
        lines.append(f"Chatbot: {truncated}")

    return "\n".join(lines) if lines else "No prior conversation."


def check_session_end_signal(intent: str) -> tuple:
    """
    Fix #1 — Tier 2 redesign, second half of the combined check.

    This is a DIFFERENT question from check_response_confidence()'s
    resolution check. Resolution asks "was this specific exchange answered
    well?" — which can be true early in a session just as easily as late.
    This asks "does the conversation's pacing look like the student is
    actually wrapping up?" — targeting the project's actual goal of
    surveying near the end of a session, not scattered throughout it.

    This is inherently an approximate signal, not a certainty — there is no
    way to directly observe "the student is about to stop" in a stateless
    chat interface. The prompt is written to reflect that: it only reports
    high likelihood when multiple pacing signals agree, and is instructed
    to stay conservative rather than overclaim.

    Returns (end_likelihood: 0-100, likely_wrapping_up: bool, reasoning: str)
    """
    if not _has_sufficient_interactions(min_count=2):
        return 0, False, "Insufficient interactions to judge session pacing"

    end_signal_prompt = _session_end_prompt()

    try:
        raw = llm_parallel.invoke(end_signal_prompt).content
        clean = raw.strip()
        if clean.startswith("```json"):
            clean = clean.replace("```json", "").replace("```", "").strip()
        elif clean.startswith("```"):
            clean = clean[3:-3].strip()
        result = json.loads(clean)
        end_likelihood = int(result.get("end_likelihood", 0))
        likely_wrapping_up = bool(result.get("likely_wrapping_up", False))
        reasoning = result.get("reasoning", "")
        print(f"[Survey] Tier 2 session-end signal: {end_likelihood}% — {reasoning}")

        # Belt-and-suspenders: require both the LLM's own boolean judgment
        # AND the numeric score clearing the threshold, so a borderline
        # "true" with a low score can't slip through.
        passes = likely_wrapping_up and end_likelihood >= SESSION_END_LIKELIHOOD_THRESHOLD
        return end_likelihood, passes, reasoning
    except Exception as e:
        print(f"[Survey] Tier 2 session-end signal check error: {e}")
        return 0, False, "Session-end signal check failed"


def _session_end_prompt() -> str:
    conversation_context = get_conversation_context()
    msg_count = st.session_state.get("msg_count", 0)
    return f"""
    You are analysing conversational PACING — not whether answers were
    correct — to estimate whether a student appears to be wrapping up their
    session with an educational chatbot, versus likely to continue asking
    more questions.

    CONVERSATION SO FAR (most recent messages):
    {conversation_context}

    MESSAGES SO FAR THIS SESSION: {msg_count}

    Consider these pacing signals together — no single one is decisive:
    - Are the student's messages getting SHORTER or less detailed over time?
      This can suggest winding down, but can also just mean they became
      comfortable with the format.
    - Is the topic NARROWING to one thing, or still EXPANDING to new,
      unrelated topics? Expanding topics suggest they are still exploring,
      not wrapping up.
    - Is there any sign of a forthcoming follow-up implied by the last
      message ("what about...", "can you also...", "one more thing")? Its
      presence suggests NOT wrapping up. Its absence is only a weak signal,
      not proof either way.
    - Does the conversation read like a single quick question-and-answer,
      or like a longer session that has covered meaningful ground and could
      plausibly be reaching a natural pause?

    IMPORTANT — this is a weak, approximate signal. Do NOT report high
    likelihood unless MULTIPLE signals point the same way. When genuinely
    unsure, prefer a LOWER likelihood — a missed opportunity to ask for
    feedback is far less costly than interrupting a student who is still
    actively working through material.

    Respond ONLY with valid JSON:
    {{
        "end_likelihood": 0 to 100,
        "likely_wrapping_up": true or false,
        "reasoning": "one sentence citing which specific pacing signals were observed"
    }}
    """


def check_response_confidence(query: str, response: str, intent: str) -> tuple:
    """
    Fix #1 — Tier 2 redesign.

    Previously this function ONLY asked "was this exchange resolved?" and
    fired Tier 2 the moment that was true — which could happen on message 2
    just as easily as message 8, not actually targeting "near the end of
    the session" as the project's stated goal requires.

    Now Tier 2 requires BOTH:
      1. The exchange was genuinely resolved (unchanged logic below —
         still necessary; a confused student should never be surveyed
         just because pacing looks like winding down), AND
      2. The conversation's pacing independently suggests the student is
         actually wrapping up (check_session_end_signal(), new).

    This keeps the original safety property (never survey an unresolved
    exchange) while adding the actual timing improvement the project set
    out to make. The return signature is unchanged — (confidence, resolved,
    reasoning) — so callers in response.py don't need to change.
    """
    if intent not in TIER2_ELIGIBLE_INTENTS:
        print(f"[Survey] Tier 2 skipped — intent '{intent}' not eligible")
        return 0, False, "Intent not eligible for Tier 2"
    if not _has_sufficient_interactions(min_count=2):
        print("[Survey] Tier 2 skipped — insufficient interactions (need 2+)")
        return 0, False, "Insufficient interactions for Tier 2"
    if _is_repeated_topic_pattern():
        print("[Survey] Tier 2 skipped — repeated topic pattern detected")
        return 0, False, "Repeated topic pattern — student may still be confused"

    confidence_prompt = _confidence_prompt(query, response, intent)

    try:
        raw = llm_parallel.invoke(confidence_prompt).content
        clean = raw.strip()
        if clean.startswith("```json"):
            clean = clean.replace("```json", "").replace("```", "").strip()
        elif clean.startswith("```"):
            clean = clean[3:-3].strip()
        result = json.loads(clean)
        confidence = int(result.get("confidence", 0))
        resolved = bool(result.get("resolved", False))
        reasoning = result.get("reasoning", "")
        print(f"[Survey] Tier 2 resolution confidence: {confidence}% — {reasoning}")

        # Fix #1 — record the raw "was the concern itself resolved" verdict
        # (distinct from the combined resolved+session-ending gate below)
        # keyed to this specific turn, so Tier 3 can tell "Tier 2 said this
        # exact concern wasn't resolved" apart from "Tier 2 was resolved but
        # decided it's not near session end yet" — only the former should
        # stop Tier 3 from firing anyway.
        st.session_state["_tier2_concern_resolved"] = (
            confidence >= TIER2_CONFIDENCE_THRESHOLD and resolved
        )
        st.session_state["_tier2_concern_resolved_turn"] = st.session_state.get("msg_count", 0)

        if not (confidence >= TIER2_CONFIDENCE_THRESHOLD and resolved):
            print(f"[Survey] Tier 2 skipped — confidence {confidence}% below threshold {TIER2_CONFIDENCE_THRESHOLD}% (session-end check skipped, no need to spend the call)")
            return confidence, False, reasoning

        # Resolution alone is no longer sufficient — also require the
        # session-end pacing signal before firing.
        end_likelihood, session_ending, end_reasoning = check_session_end_signal(intent)

        if not session_ending:
            print(f"[Survey] Tier 2 resolved (confidence {confidence}%) but session doesn't look like it's ending yet ({end_likelihood}%) — holding off")
            return confidence, False, f"Resolved, but not near session end ({end_likelihood}%): {end_reasoning}"

        print(f"[Survey] Tier 2 triggered — resolved (confidence {confidence}%) AND session-end likely ({end_likelihood}%)")
        return confidence, True, f"{reasoning} | Session pacing: {end_reasoning}"
    except Exception as e:
        print(f"[Survey] Tier 2 confidence check error: {e}")
        return 0, False, "Confidence check failed"


TIER2_ELIGIBLE_INTENTS = {"explanation", "comparison", "debug", "difficulty"}


def tier2_would_call_model(intent: str) -> bool:
    """
    Latency fix — the same pre-checks check_response_confidence() applies
    before it spends a model call, without printing or changing state, so
    answer() can start the Tier 2 calls early (see helper/llm_parallel.py).
    """
    return (intent in TIER2_ELIGIBLE_INTENTS
            and _has_sufficient_interactions(min_count=2)
            and not _is_repeated_topic_pattern())


def _confidence_prompt(query: str, response: str, intent: str) -> str:
    conversation_context = get_conversation_context()
    return f"""
    You are evaluating whether a chatbot response has successfully resolved a student's concern.

    CONVERSATION HISTORY:
    {conversation_context}

    STUDENT'S LATEST QUESTION: "{query}"
    DETECTED INTENT: {intent}

    CHATBOT'S RESPONSE:
    "{response}"

    YOUR TASK:
    Estimate how confident you are that the student's concern has been resolved.
    Return a confidence score from 0 to 100.

    HIGH CONFIDENCE (80+):
    - Student asked a clear question and response directly answered it
    - Response gave a complete explanation with a good example
    - Student asked about assessment/deadline and got a clear direct answer
    - Conversation shows student's confusion addressed progressively

    LOW CONFIDENCE (below 80):
    - Student previously said "I still don't understand"
    - Response was vague or only partially addressed the question
    - Conversation shows repeated questions on same topic
    - Intent was difficulty — one explanation may not be enough

    IMPORTANT: When in doubt, return LOW. Only return HIGH if genuinely resolved.

    Respond ONLY with valid JSON:
    {{
        "confidence": 0 to 100,
        "resolved": true or false,
        "reasoning": "one sentence explaining why"
    }}
    """


def _has_sufficient_interactions(min_count: int = 1) -> bool:
    messages = st.session_state.get("messages", [])
    if min_count == 1:
        ai_count = sum(1 for m in messages if m.get("role") == "ai")
        return ai_count >= 1
    if min_count == 2:
        msg_count = st.session_state.get("msg_count", 0)
        return msg_count >= 2
    return False


def _is_repeated_topic_pattern() -> bool:
    messages = st.session_state.get("messages", [])
    human_messages = [m for m in messages if m.get("role") == "human"]
    if len(human_messages) < 3:
        return False
    recent = human_messages[-3:]
    struggle_count = sum(
        1 for m in recent
        if is_struggle_signal(m.get("content", ""))
    )
    return struggle_count >= 2


def _is_session_limit_reached() -> bool:
    return st.session_state.get("surveys_shown_count", 0) >= 2


def _has_received_answers() -> bool:
    return st.session_state.get("msg_count", 0) >= 1


def _is_cooldown_active() -> bool:
    """
    Fix #5 — minimum-gap cooldown between survey triggers.
    Returns True if a survey fired too recently to fire again.
    """
    last_shown_at = st.session_state.get("last_survey_shown_at_msg")
    if last_shown_at is None:
        return False
    current_msg = st.session_state.get("msg_count", 0)
    gap = current_msg - last_shown_at
    if gap < SURVEY_COOLDOWN_MESSAGES:
        print(f"[Survey] Cooldown active — only {gap} messages since last survey (need {SURVEY_COOLDOWN_MESSAGES})")
        return True
    return False


def _record_survey_shown():
    """Call this whenever a non-empty survey_questions list is actually produced."""
    st.session_state["last_survey_shown_at_msg"] = st.session_state.get("msg_count", 0)
    print(f"[Survey] Recorded survey shown at message {st.session_state['last_survey_shown_at_msg']}")


def _get_recent_student_text() -> str:
    """
    Fix #4 (engagement-misattribution part) — restricts struggle /
    dissatisfaction / personal-experience scanning to the STUDENT's own
    messages. Previously these were checked against the full formatted
    conversation blob (which includes "Chatbot: ..." lines too), so the
    chatbot's own wording — e.g. explaining that a topic "challenged
    students to think deeply" — could get misread as the STUDENT reporting
    that personal experience, incorrectly unlocking engagement-category
    questions the student never actually earned.
    """
    messages = st.session_state.get("messages", [])
    recent = messages[-6:] if len(messages) >= 6 else messages
    human_lines = [
        m.get("content", "").strip()
        for m in recent
        if m.get("role") == "human" and m.get("content", "").strip()
    ]
    return " ".join(human_lines)


def query_search_intelligent(
    query: str,
    intent: str,
    all_questions: list,
    answered_questions: set = None,
    tier: int = 3,
    final_response: str = ""
) -> list:
    if not all_questions:
        print("[Survey] No questions available — skipping")
        return []

    if answered_questions is None:
        answered_questions = set()

    module_context = st.session_state.get("chatbot_context", "this course")
    # Fix #2 — includes this turn's just-generated response.
    conversation_context = get_conversation_context(final_response)
    last_substantive_query = st.session_state.get("last_substantive_query", query)
    last_substantive_intent = st.session_state.get("last_substantive_intent", intent)

    # Fix #4 — student-signal detection now reads only the student's own
    # words, not the full conversation blob (see _get_recent_student_text).
    student_text_lower = _get_recent_student_text().lower()
    last_query_lower = last_substantive_query.lower()

    student_showed_struggle = is_struggle_signal(student_text_lower)
    student_showed_dissatisfaction = is_dissatisfaction_signal(student_text_lower)
    student_shared_experience = is_personal_experience_signal(student_text_lower)

    print(f"[Survey] Student signals — struggle: {student_showed_struggle}, dissatisfaction: {student_showed_dissatisfaction}, personal experience: {student_shared_experience}")

    if student_showed_dissatisfaction:
        conversation_type = "teaching"
    elif student_showed_struggle:
        conversation_type = "teaching"
    elif student_shared_experience:
        conversation_type = "engagement"
    elif last_substantive_intent in ["explanation", "comparison", "debug", "difficulty"]:
        conversation_type = "teaching"
    elif contains_any(last_query_lower, ASSESSMENT_KEYWORDS):
        conversation_type = "assessment"
    else:
        conversation_type = "general"

    print(f"[Survey] Conversation type: {conversation_type} (intent: {last_substantive_intent}, query: '{last_substantive_query[:40]}')")
    st.session_state["last_conversation_type"] = conversation_type

    if conversation_type == "teaching":
        type_guidance = f"""
    CONVERSATION TYPE: TEACHING / EXPLANATION
    The student asked about a concept or received an explanation.
    Last query: "{last_substantive_query}"

    SELECT questions that the student can answer about:
    - Whether the explanation was clear and easy to follow
    - Whether examples or analogies helped understanding
    - Whether they feel more confident after this conversation
    - Whether the concept was explained at the right level of detail
    - Whether the explanation was concise and well-structured

    STRICTLY FORBIDDEN for TEACHING conversations — DO NOT SELECT:
    - Questions about assessment, quiz, submission, deadline, grading
    - Questions about overall chatbot satisfaction, ease of use, trust, navigation
    - Questions about personal engagement, curiosity, deep thinking, intellectual stimulation
    - Questions asking if student would use chatbot again
    - Questions asking if chatbot understood what student was asking (too generic)

    Only select these forbidden categories if ZERO teaching questions remain unanswered.
    """
    elif conversation_type == "assessment":
        type_guidance = f"""
    CONVERSATION TYPE: ASSESSMENT / LOGISTICS
    The student asked about quiz, submission, deadline, or grading.
    Last query: "{last_substantive_query}"

    SELECT questions that the student can answer about:
    - Whether the assessment information was clear
    - Whether they now know what to do next
    - Whether they feel more prepared for the assessment
    - Whether the chatbot pointed them in the right direction
    - Whether their specific assessment question was answered

    AVOID questions about: teaching clarity, personal engagement,
    general chatbot satisfaction — unless no assessment questions remain.
    """
    elif conversation_type == "engagement":
        type_guidance = f"""
    CONVERSATION TYPE: ENGAGEMENT / PERSONAL EXPERIENCE
    The student shared a personal reflection or learning experience.
    Last query: "{last_substantive_query}"

    SELECT questions that the student can answer about:
    - Whether this conversation made them think more deeply
    - Whether it sparked curiosity to learn more
    - Whether they felt engaged throughout the conversation
    - Whether it changed how they think about the topic
    - Whether they found it intellectually stimulating

    AVOID questions about: teaching clarity, assessment logistics
    — unless no engagement questions remain.
    """
    else:
        type_guidance = f"""
    CONVERSATION TYPE: GENERAL
    The student had a general course or chatbot interaction.
    Last query: "{last_substantive_query}"

    SELECT questions that the student can answer about:
    - Whether the chatbot understood what they were asking
    - Whether the response was relevant and helpful
    - Whether they trust the information given
    - Whether they would use the chatbot again
    - Whether the chatbot felt natural to use
    """

    answered_note = ""
    if answered_questions:
        answered_note = f"""
    ══════════════════════════════════════════════════
    ALREADY ANSWERED — DO NOT SELECT THESE:
    ══════════════════════════════════════════════════
    {list(answered_questions)}
    """
    else:
        answered_note = "No questions have been answered yet — all questions are available."

    tier1_note = ""
    if tier == 1:
        tier1_note = """
    IMPORTANT CONTEXT: The student has just explicitly confirmed they understood
    (e.g. said "I understand now", "thanks", "got it").
    This is an ideal moment to collect feedback — be generous in selecting questions.

    CRITICAL FOR TIER 1: Do NOT require a long conversation.
    Even a single clear question and answer exchange is sufficient to select 2 questions.
    Tier 1 fires PRECISELY because the interaction was brief and the student confirmed understanding.
    Select the 2 most relevant teaching questions even from a short exchange.
    If the student received an explanation and said thanks — that is enough evidence
    to answer questions about whether the explanation was clear and well-structured.
    """

    selection_prompt = f"""
    You are selecting survey feedback questions for a student using an educational chatbot.
    Your goal: select 2-3 questions the student can GENUINELY answer based on what they just experienced.

    MODULE: {module_context}

    FULL CONVERSATION SO FAR:
    {conversation_context}

    TRIGGER TIER: {tier}
    {tier1_note}

    STUDENT SIGNALS:
    - Showed struggle or confusion: {student_showed_struggle}
    - Showed dissatisfaction: {student_showed_dissatisfaction}
    - Shared personal experience: {student_shared_experience}

    {answered_note}

    ══════════════════════════════════════════════════
    CONVERSATION TYPE GUIDANCE:
    ══════════════════════════════════════════════════
    {type_guidance}

    ══════════════════════════════════════════════════
    AVAILABLE SURVEY QUESTIONS (select from these only):
    ══════════════════════════════════════════════════
    {all_questions}

    ══════════════════════════════════════════════════
    CRITICAL FILTERING RULES — ABSOLUTE AND NON-NEGOTIABLE:
    ══════════════════════════════════════════════════

    Read each available question carefully. Before selecting it, check ALL rules below.
    If ANY rule says NEVER — do not select that question. No exceptions.

    ── RULE 1: STRUGGLE AND CONFUSION ──
    student_showed_struggle = {student_showed_struggle}

    If student_showed_struggle = FALSE:
    NEVER select any question where the student is rating something related to:
    - Their confusion or difficulty understanding
    - The chatbot noticing they were struggling
    - The chatbot adjusting because they were confused
    - Whether their confusion was addressed
    - Whether the chatbot recognised difficulty

    If student_showed_struggle = TRUE:
    You MAY select questions about struggle and confusion if relevant.

    ── RULE 2: DISSATISFACTION ──
    student_showed_dissatisfaction = {student_showed_dissatisfaction}

    If student_showed_dissatisfaction = FALSE:
    NEVER select any question where the student is rating their dissatisfaction:
    - Whether they preferred a different approach
    - Whether they wanted something explained differently
    - Whether the explanation did not meet their expectations

    If student_showed_dissatisfaction = TRUE:
    You MAY select dissatisfaction questions if relevant.

    ── RULE 3: PERSONAL EXPERIENCE AND ENGAGEMENT ──
    student_shared_experience = {student_shared_experience}

    If student_shared_experience = FALSE:
    NEVER select any question asking about personal feelings or engagement:
    - Whether the student felt engaged
    - Whether curiosity was sparked
    - Whether they thought more deeply
    - Whether it changed how they think
    - Whether they found it intellectually stimulating

    If student_shared_experience = TRUE:
    You MAY select engagement and personal experience questions if relevant.

    ── RULE 4: ALREADY ANSWERED ──
    NEVER select any question from the already answered list above.

    ── RULE 5: CONVERSATION TYPE ──
    Follow the CONVERSATION TYPE GUIDANCE — select questions that match
    what the student actually experienced in this specific conversation.

    ── RULE 6: MINIMUM THRESHOLD ──
    If after applying Rules 1-5, fewer than 2 valid questions remain:
    Return LOW confidence. Do not force selections. No survey is better than wrong questions.
    EXCEPTION: For Tier 1 — even a single clear Q&A justifies selecting 2 teaching questions
    about whether the explanation was clear and whether the interaction helped understanding.

    ══════════════════════════════════════════════════
    CONFIDENCE DECISION:
    ══════════════════════════════════════════════════

    Return HIGH if:
    - At least 2 questions match the conversation type and student's experience
    - Student had a real substantive interaction
    - Selected questions are directly answerable from this conversation

    Return LOW if:
    - Available questions do not match the conversation type
    - Student only had a surface-level interaction
    - Fewer than 2 relevant questions available

    Respond ONLY with valid JSON:
    {{
        "confidence": "high" or "low",
        "reasoning": "explain conversation type used and why questions match",
        "selected_questions": [
            {{
                "question_text": "exact question text from the available list",
                "reasoning": "why this student can answer this right now"
            }}
        ]
    }}

    If confidence is low, set selected_questions to [].
    CRITICAL: Only select from the available questions list above.
    """

    try:
        response = st.session_state.openai_llm.invoke(selection_prompt).content
        clean_json = response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json.replace("```json", "").replace("```", "").strip()
        elif clean_json.startswith("```"):
            clean_json = clean_json[3:-3].strip()

        result = json.loads(clean_json)
        confidence = result.get("confidence", "low")
        reasoning = result.get("reasoning", "")
        selected = result.get("selected_questions", [])

        print(f"[Survey] Question selection confidence: {confidence} — {reasoning}")

        if confidence == "low" or not selected:
            print("[Survey] Question selection: low confidence — skipping survey")
            return []

        raw_selected = [q["question_text"] for q in selected if "question_text" in q]
        # Fix #4 (duplicates part) — de-dup the LLM's own selection list
        # before matching, preserving order. Previously if the model's JSON
        # listed the same question text twice, both copies passed through
        # untouched.
        raw_selected = list(dict.fromkeys(
            s.strip() for s in raw_selected if s and s.strip()
        ))

        questions = []
        seen_originals = set()
        for selected_text in raw_selected:
            selected_clean = selected_text.strip().lower()
            matched = False
            for original in all_questions:
                orig_lower = original.strip().lower()
                orig_text = orig_lower.split(":", 1)[-1].strip() if ":" in orig_lower else orig_lower
                sel_text = selected_clean.split(":", 1)[-1].strip() if ":" in selected_clean else selected_clean
                if orig_lower == selected_clean or orig_text == sel_text or sel_text in orig_text or orig_text in sel_text:
                    # Fix #4 (duplicates part, continued) — two differently
                    # worded selections can still resolve to the same
                    # canonical bank question; only keep the first.
                    if original not in seen_originals:
                        questions.append(original)
                        seen_originals.add(original)
                    matched = True
                    break
            if not matched:
                # Fix #4 (invented-questions part) — previously the
                # unmatched LLM text was kept anyway (`questions.append
                # (selected_text)`), meaning a hallucinated question outside
                # the real bank could be shown and saved as if it were a
                # real survey item. Now it's dropped instead, with a log
                # line so a pattern of hallucinated questions is visible in
                # testing rather than silently accepted.
                print(f"[Survey] ENFORCEMENT: dropped LLM-selected text not found in question bank — '{selected_text[:80]}...'")

        # ─────────────────────────────────────────────────────────────────
        # FIX #0 — Hard Python-level enforcement of category rules.
        #
        # Found via testing (professor's original Issue 2 regression check):
        # the prompt above instructs the LLM to NEVER select engagement-
        # category questions when student_shared_experience is False, but
        # this was violated in practice — e.g. an informational question
        # like "does this course challenge creativity?" (no personal
        # experience expressed) still got engagement questions selected
        # ("challenged my creativity", "challenged me to find my own
        # solutions...") because the LLM was pulled toward matching by
        # topic rather than by whether an experience was actually reported.
        #
        # A "NEVER select X" instruction in a prompt is a strong suggestion,
        # not a guarantee. This step re-checks each selected question's
        # actual category against the deterministic signals computed
        # earlier in this function, and drops any question that violates
        # the rule the LLM was supposed to follow — instead of trusting the
        # LLM to have followed it correctly.
        # ─────────────────────────────────────────────────────────────────
        enforced_questions = []
        for q in questions:
            q_category = get_question_category_resolved(q)
            violation_reason = None

            if q_category == "engagement" and not student_shared_experience:
                violation_reason = (
                    "category='engagement' but student_shared_experience=False — "
                    "student asked an informational question, did not report a "
                    "personal experience"
                )

            if violation_reason:
                print(f"[Survey] ENFORCEMENT: dropped question violating category rule — {violation_reason}")
                print(f"[Survey] ENFORCEMENT: question was: '{q[:80]}...'")
            else:
                enforced_questions.append(q)

        if len(enforced_questions) < len(questions):
            print(
                f"[Survey] ENFORCEMENT: {len(questions) - len(enforced_questions)} of "
                f"{len(questions)} selected questions dropped for violating category rules"
            )

        questions = enforced_questions

        # Rule 6 equivalent — if enforcement dropped us below the minimum
        # threshold, this is the same "no survey is better than wrong
        # questions" principle the LLM prompt already asks for, just
        # applied in code instead of trusting the LLM to self-apply it.
        # The prompt's own Rule 6 always targets 2+ selected questions
        # (including its Tier 1 exception, which still asks for 2), so the
        # minimum here matches that regardless of tier.
        min_required = 2
        if len(questions) < min_required:
            print(
                f"[Survey] ENFORCEMENT: only {len(questions)} valid question(s) remain "
                f"after enforcement (need {min_required}+) — skipping survey rather than "
                f"showing a partial/mismatched set"
            )
            return []

        print(f"[Survey] Selected {len(questions)} questions (Tier {tier})")
        return questions[:3]

    except Exception as e:
        print(f"[Survey] Question selection error: {e}")
        return []


def is_student_ready_for_survey(tier: int = 3) -> tuple:
    if not _has_received_answers():
        return False, "No answers received yet"
    if _is_session_limit_reached():
        return False, "Session survey limit reached (max 2 per session)"
    # Fix #5 — cooldown check applies to all tiers, including Tier 1.
    # "thanks" remains a valid, early trigger; this only stops it firing
    # again too soon after a survey was already shown.
    if _is_cooldown_active():
        return False, f"Cooldown active — need {SURVEY_COOLDOWN_MESSAGES}+ messages since last survey"
    if tier == 1:
        if _has_sufficient_interactions(min_count=1):
            return True, "Satisfaction signal with substantive interaction"
        else:
            return False, "No substantive interaction yet for Tier 1"
    if tier == 2:
        if _has_sufficient_interactions(min_count=2):
            return True, "Sufficient interactions for Tier 2 confidence check"
        else:
            return False, "Insufficient interactions for Tier 2 (need 2+)"
    if tier == 3:
        if st.session_state.get("student_struggling", False):
            if _has_sufficient_interactions(min_count=2):
                return True, "Student was struggling and has had multiple interactions"
            else:
                return False, "Student struggling but insufficient interactions for Tier 3"
        if _has_sufficient_interactions(min_count=2):
            return True, "Student has had multiple meaningful interactions"
        if st.session_state.get("active_scenario"):
            return True, "Student selected a scenario"
        return False, "Interactions were surface-level definitions only"
    return False, "Unknown tier"


def run_survey_pipeline(query: str, intent: str, tier: int, final_response: str = "") -> list:
    is_ready, reason = is_student_ready_for_survey(tier=tier)
    if not is_ready:
        print(f"[Survey] Tier {tier} not ready: {reason}")
        return []

    print(f"[Survey] Tier {tier} ready: {reason}")

    all_questions = search_all()
    if not all_questions:
        print("[Survey] No questions in index")
        return []

    print(f"[Survey] Total questions fetched: {len(all_questions)}")

    answered_questions = set()
    answered_texts = set()
    try:
        sql_client = st.session_state.sql_client
        chatbot_db = sql_client['chatbot']
        surveys_collection = chatbot_db['surveys']
        user_surveys = list(surveys_collection.find({"user": st.session_state.username}))
        for survey in user_surveys:
            for q in survey.get("questions", []):
                answered_questions.add(q)
                q_lower = q.strip().lower()
                q_text = q_lower.split(":", 1)[-1].strip() if ":" in q_lower else q_lower
                answered_texts.add(q_text)
        print(f"[Survey] Already answered questions: {len(answered_questions)}")
    except Exception as e:
        print(f"[Survey] DB fetch error: {e}")
        answered_questions = set()
        answered_texts = set()

    unanswered_questions = []
    for q in all_questions:
        q_lower = q.strip().lower()
        q_text = q_lower.split(":", 1)[-1].strip() if ":" in q_lower else q_lower
        is_answered = False
        for at in answered_texts:
            if q_text == at:
                is_answered = True
                break
            if len(q_text) > 20 and len(at) > 20:
                if q_text in at or at in q_text:
                    is_answered = True
                    break
        if not is_answered:
            unanswered_questions.append(q)

    print(f"[Survey] Unanswered questions available: {len(unanswered_questions)} of {len(all_questions)}")
    print(f"[Survey] Already answered (unique texts): {len(answered_texts)}")

    if not unanswered_questions:
        print("[Survey] All questions already answered — skipping")
        return []

    last_substantive_intent = st.session_state.get("last_substantive_intent", intent)
    last_substantive_query = st.session_state.get("last_substantive_query", query)
    last_query_lower = last_substantive_query.lower()

    student_showed_struggle = (
        st.session_state.get("student_struggling", False) or
        is_struggle_signal(last_query_lower)
    )
    student_showed_dissatisfaction = is_dissatisfaction_signal(last_query_lower)
    student_shared_experience = is_personal_experience_signal(last_query_lower)

    print(f"[Survey] Student signals (pipeline) — struggle: {student_showed_struggle}, dissatisfaction: {student_showed_dissatisfaction}, personal experience: {student_shared_experience}")

    if student_showed_dissatisfaction:
        pre_filter_type = "teaching"
    elif student_showed_struggle:
        pre_filter_type = "teaching"
    elif student_shared_experience:
        pre_filter_type = "engagement"
    elif last_substantive_intent in ["explanation", "comparison", "debug", "difficulty"]:
        pre_filter_type = "teaching"
    elif contains_any(last_query_lower, ASSESSMENT_KEYWORDS):
        pre_filter_type = "assessment"
    else:
        pre_filter_type = "general"

    unanswered_questions = filter_questions_by_category(unanswered_questions, pre_filter_type, min_required=2)

    selected_questions = query_search_intelligent(
        query, intent, unanswered_questions,
        answered_questions=answered_texts,
        tier=tier,
        final_response=final_response
    )

    if not isinstance(selected_questions, list) or not selected_questions:
        return []

    matches = []
    for q in selected_questions:
        q_lower = q.strip().lower()
        q_text = q_lower.split(":", 1)[-1].strip() if ":" in q_lower else q_lower
        already_answered = False
        for at in answered_texts:
            if q_text == at:
                already_answered = True
                break
            if len(q_text) > 20 and len(at) > 20:
                if q_text in at or at in q_text:
                    already_answered = True
                    break
        if not already_answered:
            matches.append(q)
        else:
            print(f"[Survey] Dedup: skipping already answered — '{q[:50]}...'")

    print(f"[Survey] Final: {len(matches)} questions after filtering answered ones")

    # Fix #5 — record when a survey is actually about to be shown, so the
    # cooldown has an accurate reference point for the next check.
    if matches:
        _record_survey_shown()

    return matches


def find_exact_matches_intersection(query: str, intent: str = "other", final_response: str = "") -> list:
    """Tier 3 entry point — contextual fallback."""
    print(f"[Survey] Tier 3 evaluating — fallback trigger")

    # Fix #1 — if Tier 2 was evaluated on THIS exact turn and explicitly
    # found the student's concern unresolved (as opposed to merely "not yet
    # near session end"), Tier 3 must not override that finding just
    # because the student has had enough messages/struggle signals overall.
    # Surveying right after telling the model itself the concern wasn't
    # resolved would ask the student to rate an interaction that's known to
    # still be unfinished.
    current_turn = st.session_state.get("msg_count", 0)
    if (
        st.session_state.get("_tier2_concern_resolved_turn") == current_turn
        and st.session_state.get("_tier2_concern_resolved") is False
    ):
        print("[Survey] Tier 3 skipped — Tier 2 found this turn's concern unresolved; not overriding")
        return []

    query_lower = (query or "").strip().lower()
    has_satisfaction = contains_any(query_lower, SATISFACTION_PHRASES)
    has_new_question = contains_any(query_lower, QUESTION_INDICATORS)

    if has_satisfaction and has_new_question:
        print(f"[Survey] Tier 3 skipped — satisfaction + new question detected, conversation still ongoing")
        return []

    return run_survey_pipeline(query, intent, tier=3, final_response=final_response)


def find_questions_tier1(query: str, intent: str, final_response: str = "") -> list:
    """Tier 1 entry point — satisfaction signal."""
    print(f"[Survey] Tier 1 triggered — satisfaction signal detected")
    return run_survey_pipeline(query, intent, tier=1, final_response=final_response)


def find_questions_tier2(query: str, intent: str, final_response: str = "") -> list:
    """Tier 2 entry point — confidence-based."""
    print(f"[Survey] Tier 2 triggered — confidence-based")
    return run_survey_pipeline(query, intent, tier=2, final_response=final_response)


def query_search(query):
    """Original function kept for backward compatibility."""
    all_questions = search_all()
    relevance_prompt = f"""
        Analyze the following user query: "{query}"
        Below is a list of available survey questions:
        {all_questions}
        Select 3-5 survey questions that are most relevant to the user's query.
        Respond in JSON format:
        {{
        "selected_questions": [
            {{
            "question_text": "Selected survey question",
            "entity_match": "Selected entity",
            "reasoning": "Short reason for selection"
            }}
        ]
        }}
        """
    response = st.session_state.openai_llm.invoke(relevance_prompt).content
    try:
        if response.startswith("```json") and response.endswith("```"):
            clean_json = response.replace('```json', '').replace('```', '').strip()
        else:
            clean_json = response
        result = json.loads(clean_json)
        return [q["question_text"] for q in result["selected_questions"]]
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e}")
        return []