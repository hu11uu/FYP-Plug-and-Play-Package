import streamlit as st
import json
import re
import time
from langchain_core.messages import HumanMessage
from helper.survey_gen import *
from helper.survey_gen import _confidence_prompt, _session_end_prompt
from helper import llm_parallel
from helper.signals import (
    GREETINGS,
    is_satisfaction_signal,
    is_struggle_signal,
    is_personal_experience_signal,
    is_urgent,
    is_clearly_unambiguous,
    is_followup_query,
)

try:
    # Fix #3 — restores semantic/vector search capability.
    from azure.search.documents.models import VectorizableTextQuery
    _VECTOR_SEARCH_AVAILABLE = True
except ImportError:
    _VECTOR_SEARCH_AVAILABLE = False

# ─────────────────────────────────────────────
# FEATURE 1: INTENT-AWARE RESPONSE ENGINE
# ─────────────────────────────────────────────

# Fix (Leftover #1) — this used to be a plain module-level dict, meaning it
# was shared across every user hitting this server process, never cleared,
# and grew unbounded for the lifetime of the process. Moved into
# st.session_state so each user gets their own isolated cache (no
# cross-user leakage), and capped in size so one very long session can't
# grow it forever either.
_INTENT_CACHE_MAX_SIZE = 200


def _get_intent_cache() -> dict:
    if "intent_cache" not in st.session_state:
        st.session_state["intent_cache"] = {}
    return st.session_state["intent_cache"]


def detect_intent(query: str) -> dict:
    """Classify the student's query into one of 6 intent types."""
    key = (query or "").strip().lower()
    if not key:
        return {"intent": "other", "reasoning": "Empty query"}

    intent_cache = _get_intent_cache()
    if key in intent_cache:
        return intent_cache[key]
    if "openai_llm" not in st.session_state:
        return {"intent": "other", "reasoning": "LLM not ready"}

    classifier_prompt = _intent_prompt(query)

    try:
        raw = llm_parallel.invoke(classifier_prompt).content
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        valid_intents = {"definition", "comparison", "explanation", "debug", "difficulty", "other"}
        if result.get("intent") not in valid_intents:
            result["intent"] = "other"
    except Exception:
        result = {"intent": "other", "reasoning": "Classification failed"}

    # Cap size — if a single session runs long enough to hit the cap,
    # just drop the oldest entry rather than growing forever.
    if len(intent_cache) >= _INTENT_CACHE_MAX_SIZE:
        oldest_key = next(iter(intent_cache))
        del intent_cache[oldest_key]

    intent_cache[key] = result
    return result


def _intent_prompt(query: str) -> str:
    return f"""
    You are a query intent classifier for an educational chatbot.
    Classify the student's query into EXACTLY ONE of these intents:

    - "definition"  : Student wants to know what something is. e.g. "what is a linked list", "define recursion", "what is sc1003"
    - "comparison"  : Student wants to compare two or more things. e.g. "difference between array and linked list", "X vs Y"
    - "explanation" : Student wants to understand how something works. e.g. "explain recursion", "how does a for loop work"
    - "debug"       : Student has broken code or logic. e.g. "why is my loop not working", "fix my function"
    - "difficulty"  : Student is confused or struggling. e.g. "I don't understand pointers", "I'm confused about functions"
    - "other"       : Anything else — greetings, course admin, general queries

    Student query: "{query}"

    Respond ONLY with valid JSON:
    {{
        "intent": "definition" | "comparison" | "explanation" | "debug" | "difficulty" | "other",
        "reasoning": "one sentence explaining why"
    }}
    """


# ─────────────────────────────────────────────
# FEATURE 3: DIFFICULTY DETECTION SYSTEM
# ─────────────────────────────────────────────

def detect_difficulty(query: str) -> bool:
    """
    Analyse the last 3-4 conversation messages to detect if the student
    is struggling — even if their current query doesn't explicitly say so.

    Returns True if struggle pattern detected, False otherwise.
    Stores result in st.session_state.student_struggling.

    Note: struggle detection still runs for response style adaptation (Feature 3).
    It no longer directly triggers the survey — that is handled by the three-tier system.
    """
    # Fix #7 — uses shared is_struggle_signal() instead of a local keyword list.
    if is_struggle_signal(query):
        print(f"Difficulty detected: explicit phrase match")
        st.session_state.student_struggling = True
        return True

    past_messages = st.session_state.conversation[-8:-1].copy() if len(st.session_state.conversation) > 1 else []

    if not past_messages or "openai_llm" not in st.session_state:
        st.session_state.student_struggling = False
        return False

    if len(past_messages) < 2:
        st.session_state.student_struggling = False
        return False

    difficulty_prompt = _difficulty_prompt(query, past_messages)

    try:
        raw = llm_parallel.invoke(difficulty_prompt).content
        result = _parse_json_response(raw)

        if result and result.get("struggling"):
            print(f"Difficulty detected from history: {result.get('reason', '')}")
            st.session_state.student_struggling = True
            return True

    except Exception as e:
        print(f"Difficulty detection error: {e}")

    st.session_state.student_struggling = False
    return False


def _difficulty_prompt(query: str, past_messages: list) -> str:
    return f"""
    You are analysing a student's conversation history to detect if they are GENUINELY STRUGGLING.

    Recent conversation history:
    {past_messages}

    Current query: "{query}"

    GENUINE STRUGGLE — mark struggling: true ONLY if:
    1. Student explicitly says they don't understand AFTER receiving an explanation:
       "I still don't get it", "I'm still confused", "that didn't help", "can you explain again"
    2. Student gives frustrated short responses: "what?", "huh?", "ok but why?", "still lost"
    3. Student asks the SAME question again after getting an answer — same wording, same meaning
    4. Student's questions are getting MORE basic, suggesting the answer made things worse

    NATURAL LEARNING — mark struggling: false if:
    1. Student asks a follow-up that BUILDS on the previous answer — NORMAL learning
    2. Student asks a deeper or more advanced question on the same topic — DEEPER learning
    3. Student is exploring related concepts naturally
    4. Student's questions show they understood and want to know MORE

    KEY RULE: Asking multiple questions on the same topic is NOT struggling.
    Only mark struggling: true if there is clear evidence the student did NOT understand
    a previous explanation and needs it re-explained.

    Respond ONLY with valid JSON:
    {{
        "struggling": true or false,
        "reason": "one sentence with specific evidence from the conversation"
    }}
    """


def get_response_constraints(intent: str) -> dict:
    """Return response formatting constraints based on detected intent."""
    constraints = {
        "definition": {
            "max_sentences": 2,
            "max_words": 50,
            "allow_bullets": False,
            "tone": "concise and clear",
            "instruction": "Answer in 1-2 sentences MAXIMUM. Give a simple, direct definition only. Do NOT elaborate. Do NOT add examples. Do NOT add bullet points. Do NOT add code. HARD LIMIT: 50 words."
        },
        "comparison": {
            "max_sentences": 6,
            "max_words": 150,
            "allow_bullets": True,
            "tone": "structured and clear",
            "instruction": "Compare the two things clearly. Use bullet points for key differences. Keep it under 150 words. Do NOT add resources or links."
        },
        "explanation": {
            "max_sentences": 5,
            "max_words": 120,
            "allow_bullets": True,
            "tone": "conversational and educational",
            "instruction": "Explain naturally like a knowledgeable friend. Use one simple example if it helps. Keep it under 120 words. Do NOT add resources or links."
        },
        "debug": {
            "max_sentences": 8,
            "max_words": 180,
            "allow_bullets": True,
            "tone": "step-by-step and helpful",
            "instruction": "Walk through the problem step by step. Identify the issue and explain how to fix it. Keep it under 180 words."
        },
        "difficulty": {
            "max_sentences": 5,
            "max_words": 120,
            "allow_bullets": False,
            "tone": "simple, patient and encouraging",
            "instruction": "The student is struggling. Use the simplest possible language. Use a real-world analogy. Be encouraging. Keep it under 120 words."
        },
        "satisfaction": {
            "max_sentences": 2,
            "max_words": 30,
            "allow_bullets": False,
            "tone": "warm and brief",
            "instruction": "Student just confirmed they understood. Give a brief warm acknowledgement only. 1-2 sentences maximum. Do NOT elaborate or add new information."
        },
        "other": {
            "max_sentences": 3,
            "max_words": 80,
            "allow_bullets": False,
            "tone": "conversational and helpful",
            "instruction": "Answer naturally and conversationally. Keep it brief. Under 80 words."
        }
    }
    return constraints.get(intent, constraints["other"])


def enforce_brevity(query: str, text: str, intent: str) -> str:
    """
    Hard-trim the response to match intent constraints.
    Strips bullets, code blocks, and extra formatting before trimming.
    """
    if not text:
        return text

    text = text.replace("\\n", "\n").replace("\\t", " ")

    # Fix #6 — previously this unconditionally stripped every fenced code
    # block before trimming, for every intent including "debug". That could
    # delete the actual code fix from a debugging answer. A response
    # containing code is exactly the case brevity-trimming is least safe
    # to apply (trimming mid-block, or cutting the block's surrounding
    # explanation) — so skip trimming entirely whenever code is present,
    # regardless of intent, and return the answer as-is.
    if "```" in text:
        return text.strip()

    constraints = get_response_constraints(intent)
    max_sentences = constraints["max_sentences"]
    max_words = constraints["max_words"]

    text = re.sub(r"#{1,6}\s+.*", "", text)

    lines = text.splitlines()
    flat_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[-*•·]\s+", stripped):
            stripped = re.sub(r"^[-*•·]\s+", "", stripped)
            if stripped and stripped[-1] not in ".!?":
                stripped += "."
        flat_lines.append(stripped)

    text = " ".join(flat_lines)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    trimmed = " ".join(sentences[:max_sentences])

    words = trimmed.split()
    if len(words) > max_words:
        trimmed = " ".join(words[:max_words]).rstrip()
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."

    return trimmed.strip()


def _course_label() -> str:
    """
    Short course name for user-facing text, taken from chatbot_context.

    chatbot_context is free text set by staff, so only its first line is
    used, anything from the first "(" onwards is dropped, and a missing or
    very long value falls back to "this course" rather than pasting a whole
    paragraph into a greeting.
    """
    context = st.session_state.get("chatbot_context") or ""
    first_line = str(context).strip().splitlines()[0] if str(context).strip() else ""
    label = first_line.split("(")[0].strip().rstrip(".,;:")
    if not label or len(label) > 80:
        return "this course"
    return label


def _parse_json_response(response: str) -> dict:
    """Shared helper to parse LLM JSON responses safely."""
    try:
        clean = response.strip()
        if clean.startswith("```json") and clean.endswith("```"):
            clean = clean.replace("```json", "").replace("```", "").strip()
        elif clean.startswith("```") and clean.endswith("```"):
            clean = clean[3:-3].strip()
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e}")
        return None


# ─────────────────────────────────────────────
# TIER 1: SATISFACTION SIGNAL DETECTION
# ─────────────────────────────────────────────

def detect_satisfaction_signal(query: str) -> bool:
    """
    TIER 1 — Detect if the student explicitly confirmed understanding.
    Fix #7 — delegates to the shared is_satisfaction_signal() implementation
    instead of a locally duplicated keyword list.
    """
    result = is_satisfaction_signal(query)
    if result:
        print(f"[Survey] Tier 1: Satisfaction signal detected in: '{query}'")
    return result


def generate_satisfaction_response(query: str) -> str:
    """
    Generate a brief warm response when Tier 1 satisfaction signal detected.
    Response should be short — student already understood, no need to elaborate.
    """
    satisfaction_prompt = f"""
    A student just confirmed they understood the explanation.
    Their message: "{query}"

    Give a brief, warm, natural acknowledgement.
    RULES:
    - Maximum 2 sentences
    - Do NOT repeat or re-explain anything
    - Do NOT add new information
    - Sound like a friendly TA
    - Keep it under 30 words

    Examples of good responses:
    - "Glad that helped! Feel free to ask if anything else comes up."
    - "Great, happy to help!"
    - "Good to hear — let me know if you have more questions."

    Respond with just the acknowledgement text, no JSON needed.
    """

    try:
        response = st.session_state.openai_llm.invoke(satisfaction_prompt).content
        return enforce_brevity(query, response.strip(), "satisfaction")
    except Exception as e:
        print(f"Satisfaction response error: {e}")
        return "Glad that helped! Feel free to ask if anything else comes up."


# ─────────────────────────────────────────────
# EXISTING FUNCTIONS
# ─────────────────────────────────────────────

def generate_conversation_title(query):
    title_response = llm_parallel.invoke(_title_prompt(query)).content
    st.session_state.new_conversation = False
    return title_response


def _title_prompt(query) -> str:
    return f"""
    Generate a short, relevant, and engaging title for a conversation based on the user's query: "{query}". 
    The title should reflect the topic or theme of the question in a concise manner, 
    capturing the essence of the conversation while being easily understandable. 
    The title should be formatted as a sentence or phrase, ideally between 3 to 6 words.
    """


def _build_vector_search_kwargs(query: str, base_kwargs: dict) -> dict:
    """
    Fix #3 — shared helper to attach a vector query to a search call when the
    Azure Search SDK's vector query type is available. Falls back silently to
    keyword-only search (base_kwargs unchanged) if not, so retrieval never
    breaks outright — it just loses the semantic-matching benefit.
    """
    if not _VECTOR_SEARCH_AVAILABLE:
        return base_kwargs
    try:
        vector_query_content = VectorizableTextQuery(
            text=query,
            k_nearest_neighbors=50,
            fields="content_vector"
        )
        base_kwargs["vector_queries"] = [vector_query_content]
    except Exception as ve:
        print(f"[Response] Vector query construction failed, falling back to keyword-only: {ve}")
    return base_kwargs


def is_relevant_to_information(query, intent: str = "other"):
    """Search information index and return answer based on detected intent."""
    contexts = ""
    try:
        search_kwargs = _build_vector_search_kwargs(query, {"search_text": query, "top": 10})
        context = st.session_state.information_search.search(**search_kwargs)
        for con in context:
            if 'content' in con:
                contexts += con['content'] + "\n"
            elif 'text' in con:
                contexts += con['text'] + "\n"
            else:
                for key, value in con.items():
                    if isinstance(value, str):
                        contexts += str(value) + "\n"
                        break
        print(contexts)
    except Exception as e:
        # Fix (patch) — a vector query can fail at SEARCH time (not just
        # construction time) if the index's vector field has no vectorizer
        # configured, e.g. Azure error "Field 'content_vector' does not have
        # a vectorizer defined in its vector profile". Previously this
        # silently produced empty context for the whole request. Now we
        # retry once with keyword-only search so a missing vectorizer
        # degrades to the old (working) behavior instead of losing
        # retrieval entirely.
        print(f"Error searching information (with vector query): {e}")
        try:
            print("Retrying information search without vector query...")
            context = st.session_state.information_search.search(search_text=query, top=10)
            for con in context:
                if 'content' in con:
                    contexts += con['content'] + "\n"
                elif 'text' in con:
                    contexts += con['text'] + "\n"
                else:
                    for key, value in con.items():
                        if isinstance(value, str):
                            contexts += str(value) + "\n"
                            break
            print(contexts)
        except Exception as e2:
            print(f"Error searching information (keyword-only retry also failed): {e2}")
            contexts = ""

    constraints = get_response_constraints(intent)

    information_relevance_prompt = f"""
    You are a helpful teaching assistant. Use the context below to answer the student's query.
    IMPORTANT: You are a TA, not a student. Never say "in my experience", "I feel", or speak as if you are enrolled in the course. Always respond from a TA's perspective.

    Student query: "{query}"
    Detected intent: {intent}
    Context: {contexts}

    STRICT Response rules:
    - Always respond in English regardless of the language of the context.
    - Tone: {constraints['tone']}
    - {constraints['instruction']}
    - {"You MAY use bullet points." if constraints['allow_bullets'] else "Do NOT use bullet points, dashes, or lists of any kind."}
    - Do NOT include resources, links, or YouTube videos unless explicitly asked.
    - Do NOT add code examples unless the intent is 'debug' or the student explicitly asks for code.
    - If the context doesn't have enough information to answer, set "relevant" to false.

    Respond in JSON format:
    {{
        "relevant": true/false,
        "response": "your answer here — follow the strict rules above, HARD LIMIT on words"
    }}

    If relevant is false, set response to "I'm sorry, I don't have enough context to answer that question."
    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    raw = st.session_state.openai_llm.invoke(information_relevance_prompt).content
    result = _parse_json_response(raw)

    if result is None:
        return {"relevant": False, "response": "An error occurred while processing your request. Please try again later."}

    if result.get("relevant") and result.get("response"):
        result["response"] = enforce_brevity(query, result["response"], intent)

    return result


def is_relevant_to_notes(query, intent: str = "other"):
    """Search notes index and return answer based on detected intent."""
    contexts = ""
    try:
        search_kwargs = _build_vector_search_kwargs(query, {"search_text": query, "top": 10})
        context = st.session_state.notes_search.search(**search_kwargs)
        for con in context:
            if 'content' in con:
                contexts += con['content'] + "\n"
            elif 'text' in con:
                contexts += con['text'] + "\n"
            else:
                for key, value in con.items():
                    if isinstance(value, str):
                        contexts += str(value) + "\n"
                        break
    except Exception as e:
        # Same fix as is_relevant_to_information — retry keyword-only if the
        # vector query fails at search time (e.g. missing vectorizer on the
        # index's vector profile), instead of silently losing all context.
        print(f"Error searching notes (with vector query): {e}")
        try:
            print("Retrying notes search without vector query...")
            context = st.session_state.notes_search.search(search_text=query, top=10)
            for con in context:
                if 'content' in con:
                    contexts += con['content'] + "\n"
                elif 'text' in con:
                    contexts += con['text'] + "\n"
                else:
                    for key, value in con.items():
                        if isinstance(value, str):
                            contexts += str(value) + "\n"
                            break
        except Exception as e2:
            print(f"Error searching notes (keyword-only retry also failed): {e2}")
            contexts = ""

    constraints = get_response_constraints(intent)

    notes_relevance_prompt = f"""
    You are a helpful teaching assistant. Use the context below to answer the student's query.
    IMPORTANT: You are a TA, not a student. Never say "in my experience", "I feel", or speak as if you are enrolled in the course. Always respond from a TA's perspective.

    Student query: "{query}"
    Detected intent: {intent}
    Context: {contexts}

    STRICT Response rules:
    - Always respond in English regardless of the language of the context.
    - Tone: {constraints['tone']}
    - {constraints['instruction']}
    - {"You MAY use bullet points." if constraints['allow_bullets'] else "Do NOT use bullet points, dashes, or lists of any kind."}
    - Do NOT include resources, links, or YouTube videos unless explicitly asked.
    - Do NOT add code examples unless the intent is 'debug' or the student explicitly asks for code.
    - If the context doesn't have enough information to answer, set "relevant" to false.

    Respond in JSON format:
    {{
        "relevant": true/false,
        "response": "your answer here — follow the strict rules above, HARD LIMIT on words"
    }}

    If relevant is false, set response to "I'm sorry, I don't have enough context to answer that question."
    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    raw = st.session_state.openai_llm.invoke(notes_relevance_prompt).content
    result = _parse_json_response(raw)

    if result is None:
        return {"relevant": False, "response": "An error occurred while processing your request. Please try again later."}

    if result.get("relevant") and result.get("response"):
        result["response"] = enforce_brevity(query, result["response"], intent)

    return result


def is_relevant_to_context(query):
    """
    Check if query is within the chatbot's domain.

    Fix #1 — the hardcoded CS_KEYWORDS shortcut has been removed. It was
    matching ordinary English words ("list", "set", "class", "return",
    "code", "program") and letting off-topic queries bypass this check
    entirely. It was also SC1003-specific, so it silently stopped being
    useful the moment the chatbot was reconfigured for any other module.
    Every query now goes through the LLM domain check below, which is
    module-agnostic and doesn't have the false-positive problem.
    """
    if isinstance(query, list):
        query = " ".join([str(q) for q in query])
    query = str(query)

    domain = st.session_state.chatbot_context

    primary_prompt = _domain_filter_prompt(query)
    primary_response = llm_parallel.invoke(primary_prompt).content.lower()

    if "yes" in primary_response:
        return True

    fallback_prompt = f"""
    Final check, using the same test as before — would answering this
    query require any knowledge specific to {domain}, programming,
    computational thinking, or this student's coursework? Phrasing (e.g.
    "help me with X") does not matter — only whether X itself needs
    course-specific knowledge to answer.

    If the query is a bare term that has BOTH an everyday meaning and a
    recognized {domain}/CS meaning (e.g. "queue", "stack", "tree",
    "class"), and nothing else in the query points toward the everyday
    meaning, default to Yes — this chatbot only exists inside a {domain}
    course, so a student asking about such a term is almost certainly
    asking about its course-relevant meaning. This does NOT apply to
    unambiguous compound CS terms like "linked list" or "binary tree" —
    those have only one meaning and are always Yes.

    Query: "{query}"

    Respond ONLY with 'Yes' or 'No'.
    """
    fallback_response = llm_parallel.invoke(fallback_prompt).content

    if "yes" in fallback_response:
        return True

    return False


def _domain_filter_prompt(query) -> str:
    if isinstance(query, list):
        query = " ".join([str(q) for q in query])
    query = str(query)
    domain = st.session_state.chatbot_context
    return f"""
    You are a domain filter for an educational chatbot about {domain}.

    Query: "{query}"

    THE TEST — ask yourself this ONE question:
    Would answering this query require knowledge about {domain}, Python
    programming, computational thinking, or this student's own coursework
    (assessments, deadlines, labs, projects, quizzes, materials, or how
    this course/module affects them)?

    If YES — the query is in scope. This is true no matter how it's
    phrased. A question doesn't become off-topic because it's worded as
    "help me with X" or "can this benefit me" or any other personal
    framing — what matters is whether X itself is something a TA for this
    course would actually need to answer. "Help me with the quiz",
    "does this course help my problem-solving", "I need help with my
    assignment" all require course knowledge to answer, so they are ALL
    in scope, even though none of them use the literal words "this
    course" or "this module".

    If NO — answering it needs no course-specific knowledge at all (it's
    about sports, food, weather, finance, relationships, entertainment,
    or anything else a student would just Google) — the query is out of
    scope, regardless of phrasing.

    When genuinely unsure which side a query falls on, ask: "is there
    anything in this query — a topic, an assignment, an assessment
    component, this course/module itself — that only makes sense in the
    context of {domain}?" If yes, answer Yes. Only answer No when nothing
    about the query requires this course's context to make sense.

    SPECIAL CASE — terms with a dual meaning. Some words are BOTH a
    recognized {domain}/CS concept AND an everyday English word (e.g.
    "queue" means a waiting line in everyday speech, but is also a core
    data structure; "stack", "tree", "class", "node" have the same
    pattern). When a bare query like "what is a queue?" gives no other
    context, do NOT default away from the course just because the word
    also has an everyday meaning. This chatbot only exists inside a
    {domain} course — a student typing into it is asking about the
    course, not requesting general trivia, unless something in the query
    clearly signals otherwise (e.g. "what is the queue like at the
    canteen?" clearly means the everyday sense, and should be No). For a
    bare "what is a queue?" with nothing else pointing away from the
    course, answer Yes — default TOWARD the course-relevant meaning for
    these dual-meaning terms, not away from it.

    A few illustrative examples (not an exhaustive list — apply the TEST
    above to anything not shown here):
    "what is recursion?" → Yes (needs course/programming knowledge)
    "when is the quiz?" → Yes (needs course knowledge)
    "I need help with the quiz" → Yes (still needs course knowledge to answer, "help me" phrasing doesn't change that)
    "does this course challenge creativity?" → Yes (needs course knowledge)
    "what is a queue?" → Yes (dual-meaning term — default to the CS meaning with no other context, see SPECIAL CASE above)
    "what is a stack?" → Yes (same reasoning — data structure, not "a pile of things")
    "what is a linked list?" → Yes (a standard, unambiguous CS data structure — this is NOT the same as the bare word "list" discussed in SPECIAL CASE, it's a clear technical term with only one meaning)
    "what is a binary tree?" → Yes (unambiguous CS data structure)
    "who won the World Cup?" → No (needs zero course knowledge)
    "how do I lose weight?" → No (needs zero course knowledge — a generic health question, unrelated to the course)
    "can you write a love letter?" → No (needs zero course knowledge)

    Respond ONLY with 'Yes' or 'No'.
    """


def generate_relevant_answer_with_links(query, notes_response, intent: str = "other"):
    """Generate answer using notes context, styled by intent."""
    constraints = get_response_constraints(intent)

    answer_prompt = f"""
    You are a helpful teaching assistant. Answer the student's query naturally and conversationally.
    IMPORTANT: You are a TA, not a student. Never say "in my experience", "I feel", or speak as if you are enrolled in the course. Always respond from a TA's perspective.

    Student query: "{query}"
    Detected intent: {intent}
    Additional context from notes: {notes_response}

    STRICT Response rules:
    - Always respond in English regardless of the language of the context.
    - Tone: {constraints['tone']}
    - {constraints['instruction']}
    - Use the notes context to supplement your answer if relevant, but don't force it in.
    - {"You MAY use bullet points." if constraints['allow_bullets'] else "Do NOT use bullet points, dashes, or lists of any kind."}
    - Do NOT include resources, links, or YouTube videos unless explicitly asked.
    - Do NOT add code examples unless the intent is 'debug' or the student explicitly asks for code.
    - Write like a knowledgeable friend or TA, not a textbook.

    Respond in JSON format:
    {{
        "response": "your answer here — follow the strict rules above"
    }}

    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    raw = st.session_state.openai_llm.invoke(answer_prompt).content
    result = _parse_json_response(raw)

    if result and result.get("response"):
        return enforce_brevity(query, result["response"], intent)

    return enforce_brevity(query, raw, intent)


def if_related_to_past_conversations(query):
    """Check if the current query is a follow-up to past messages."""
    past_messages = _past_conversation_messages()

    if not past_messages:
        return {"is_related": False, "relevant_query": "", "relevant_response": ""}

    response = llm_parallel.invoke(_past_conversation_prompt(query, past_messages)).content
    print("past convo", response)

    result = _parse_json_response(response)
    if result is None:
        return {"is_related": False, "relevant_query": "", "relevant_response": ""}
    return result


def _past_conversation_messages():
    return st.session_state.conversation[-8:-1].copy() if len(st.session_state.conversation) > 1 else []


def _past_conversation_prompt(query, past_messages) -> list:
    context_query = f"""
    Analyze if the current query continues or relates to the previous conversations.

    ### Previous Conversations [ordered chronologically from the earliest (top) to the latest (bottom)]:
    {past_messages}

    ### Current Query: "{query}"
    
    ### Analysis Criteria:
    1. LINGUISTIC CONTINUITY
       - Contains follow-up phrases: "tell me more", "explain further", "elaborate", etc.
       - Uses pronouns referring to previous content (it, that, this, those, etc.)
       - Contains elliptical expressions (incomplete sentences that rely on previous context)
    
    2. SEMANTIC CONTINUITY
       - References specific terms, concepts, or entities mentioned in previous messages
       - Asks questions about previously discussed topics
       - Requests examples or details about a previously introduced subject
       - Builds upon or challenges information provided earlier
    
    3. FUNCTIONAL CONTINUITY
       - Answers a question posed in the previous messages
       - Provides requested information from previous turns
       - Refers to a requested action mentioned earlier

    Return a JSON object with the following structure:
    {{
        "is_related": true/false,
        "relevant_query": "If related, include the specific query or queries that are relevant to the current query. If not related, leave this empty.",
        "relevant_response": "If related, include the specific parts of previous conversations that are relevant to the current query. Extract only the most essential information needed to understand the context of the current query. If not related, leave this empty."
    }}
    
    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """
    return [HumanMessage(content=context_query)]


def continue_from_previous_conversation(query, conversation_history, info_reponse, intent: str = "other"):
    """Continue conversation from previous context, styled by intent."""
    constraints = get_response_constraints(intent)

    context_query = f"""
    You are a helpful teaching assistant continuing a conversation with a student.

    Previous context: {conversation_history}
    Current query: "{query}"
    Detected intent: {intent}
    Additional information (if needed): {info_reponse}

    STRICT Response rules:
    - Always respond in English regardless of the language of the context.
    - Tone: {constraints['tone']}
    - {constraints['instruction']}
    - Seamlessly connect to the previous context without being repetitive.
    - {"You MAY use bullet points." if constraints['allow_bullets'] else "Do NOT use bullet points, dashes, or lists of any kind."}
    - Do NOT include resources, links, or YouTube videos unless explicitly asked.
    - Do NOT praise the student or say things like "great question", "nice job", "good thinking".
    - Do NOT reference things the student never said or did.
    - Do NOT be patronising or overly enthusiastic.
    - Just answer naturally like a helpful TA would.

    Respond in JSON format:
    {{
        "response": "your answer here — follow the strict rules above"
    }}

    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    raw = st.session_state.openai_llm.invoke([HumanMessage(content=context_query)]).content
    result = _parse_json_response(raw)

    if result and result.get("response"):
        return enforce_brevity(query, result["response"], intent)

    return enforce_brevity(query, raw, intent)


# ─────────────────────────────────────────────
# FEATURE 2: PROACTIVE LEARNING PATH SUGGESTER
# ─────────────────────────────────────────────

def suggest_follow_up_topics(query: str, response: str, intent: str) -> str:
    """
    Generate 2-3 natural follow-up topic suggestions.
    Stores in st.session_state.follow_up_suggestions for UI rendering.
    Returns the original response unchanged.
    """
    st.session_state.follow_up_suggestions = []

    if intent in _SUGGESTER_SKIP_INTENTS:
        return response

    suggester_prompt = _suggester_prompt(query, response, intent)

    try:
        raw = llm_parallel.invoke(suggester_prompt).content
        result = _parse_json_response(raw)

        if result and result.get("suggestions"):
            st.session_state.follow_up_suggestions = result["suggestions"][:3]
            print(f"Follow-up suggestions: {st.session_state.follow_up_suggestions}")

    except Exception as e:
        print(f"Follow-up suggestion error: {e}")

    return response


_SUGGESTER_SKIP_INTENTS = {"debug", "other", "satisfaction"}


def _suggester_prompt(query: str, response: str, intent: str) -> str:
    domain = st.session_state.chatbot_context
    return f"""
    A student studying {domain} just asked this question and received this answer.

    Student question: "{query}"
    Answer given: "{response}"
    Intent type: {intent}

    Your task: Suggest 2-3 natural follow-up topics the student might want to explore next.

    Rules:
    - Suggestions must be directly related to the current topic
    - Each suggestion should be phrased as a short question the student might ask next
    - Keep each suggestion under 10 words
    - Make them feel natural, like a TA casually suggesting next steps
    - Do NOT repeat what was already answered
    - Do NOT suggest unrelated topics

    Respond ONLY with valid JSON:
    {{
        "suggestions": [
            "First follow-up question",
            "Second follow-up question",
            "Third follow-up question"
        ]
    }}
    """


# ─────────────────────────────────────────────
# FEATURE 4: SCENARIO DERIVATION ENGINE
# ─────────────────────────────────────────────

def derive_scenarios(query: str) -> dict:
    """
    Determine if the query is ambiguous and generate 2-3 scenario options.
    Fix #7 — is_urgent()/is_clearly_unambiguous() now come from the shared
    signals module instead of local keyword lists.
    """
    if is_urgent(query) or is_clearly_unambiguous(query):
        return {"is_ambiguous": False, "scenarios": []}

    if "openai_llm" not in st.session_state:
        return {"is_ambiguous": False, "scenarios": []}

    scenario_prompt = _scenario_prompt(query)

    try:
        raw = llm_parallel.invoke(scenario_prompt).content
        result = _parse_json_response(raw)
        if result is None:
            return {"is_ambiguous": False, "scenarios": []}

        is_ambiguous = bool(result.get("is_ambiguous", False))
        scenarios = result.get("scenarios", [])

        if is_ambiguous and len(scenarios) >= 2:
            return {"is_ambiguous": True, "scenarios": scenarios[:3]}
        else:
            return {"is_ambiguous": False, "scenarios": []}

    except Exception as e:
        print(f"Scenario derivation error: {e}")
        return {"is_ambiguous": False, "scenarios": []}


def _scenario_prompt(query: str) -> str:
    past_messages = st.session_state.conversation[-6:].copy() if len(st.session_state.conversation) > 0 else []
    domain = st.session_state.chatbot_context
    return f"""
    You are helping an educational chatbot understand what a student needs.

    Domain: {domain}
    Recent conversation: {past_messages}
    Student query: "{query}"

    Your task:
    1. Determine if this query is AMBIGUOUS
    2. If ambiguous, generate 2-3 SHORT scenario options the student might be in

    A query is ambiguous ONLY if the CORRECT ANSWER would genuinely differ
    depending on which of several distinct situations the student is in —
    not merely because the topic invites elaboration, reflection, or a
    longer answer.

    A query is NOT ambiguous if:
    - It's asking for a clear definition or explanation
    - It has a single clear answer regardless of situation
    - It's a simple factual question
    - It's an open-ended question about a general course attribute (e.g.
      "does this course challenge creativity?", "is the mini-project
      engaging?") that has ONE comprehensive answer covering the whole
      topic — even if that answer is long or touches multiple angles, it
      is still ONE answer, not several different answers for different
      students. Do NOT mark these as ambiguous just because you could
      imagine different personal angles a student might care about — that
      is a difference in what to EMPHASIZE, not a difference in what is
      CORRECT.

    CRITICAL — if you do mark something ambiguous, each scenario option
    must represent a genuinely DIFFERENT situation requiring a
    MATERIALLY DIFFERENT answer. Do NOT generate scenarios that are just
    reworded restatements of the original query (e.g. for "does this
    course challenge creativity?", options like "curious if course sparks
    creativity" or "interested in creative projects" are NOT valid
    scenarios — they don't change what the correct answer would be, they
    just rephrase the same question). If you cannot think of scenarios
    that would each require a substantively different response, the query
    is NOT ambiguous — return is_ambiguous: false instead of forcing
    invented options.

    IMPORTANT — TONE DETECTION:
    If the student uses positive language — generate scenarios that reflect POSITIVE engagement.
    If the student uses negative language — generate struggle-based scenarios.

    Keep scenario options SHORT — max 6 words each.

    Respond ONLY with valid JSON:
    {{
        "is_ambiguous": true/false,
        "scenarios": ["scenario 1", "scenario 2", "scenario 3"]
    }}

    If not ambiguous, set scenarios to empty list [].
    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """


def answer_with_scenario(query: str, scenario: str, intent: str) -> str:
    """Generate an answer tailored to the specific scenario the student selected."""
    constraints = get_response_constraints(intent)

    info_response = is_relevant_to_information(query, intent)
    if info_response["relevant"]:
        context = info_response["response"]
    else:
        notes_response = is_relevant_to_notes(query, intent)
        context = notes_response["response"] if notes_response["relevant"] else "None"

    scenario_answer_prompt = f"""
    You are a helpful teaching assistant. A student asked a question and selected a specific scenario.

    Student query: "{query}"
    Student's specific situation: "{scenario}"
    Available context: {context}

    Answer SPECIFICALLY for the student's situation: "{scenario}"
    Do not give a generic answer — tailor it exactly to what they need.

    STRICT Response rules:
    - Always respond in English.
    - Tone: {constraints['tone']}
    - {constraints['instruction']}
    - {"You MAY use bullet points." if constraints['allow_bullets'] else "Do NOT use bullet points."}
    - Do NOT include resources, links, or YouTube videos unless explicitly asked.

    Respond in JSON format:
    {{
        "response": "your scenario-specific answer here"
    }}
    """

    try:
        raw = st.session_state.openai_llm.invoke(scenario_answer_prompt).content
        result = _parse_json_response(raw)
        if result and result.get("response"):
            final_response = enforce_brevity(query, result["response"], intent)
            return suggest_follow_up_topics(query, final_response, "explanation")
    except Exception as e:
        print(f"Scenario answer error: {e}")

    return generate_relevant_answer_with_links(query, "None", intent)


# ─────────────────────────────────────────────
# MAIN ANSWER FUNCTION
# Three-tier survey triggering system
# ─────────────────────────────────────────────

def answer(query):
    """
    Entry point, unchanged in behaviour. The turn_scope() wrapper only gives
    this turn a place to keep calls started early by the latency fix (see
    helper/llm_parallel.py); anything not used is discarded when it ends.
    """
    started = time.perf_counter()
    try:
        with llm_parallel.turn_scope():
            _prefetch_turn_start(query)
            return _answer(query)
    finally:
        mode = "on" if llm_parallel.PARALLEL_LLM_CALLS else "off"
        print(f"[Latency] Reply ready in {time.perf_counter() - started:.2f}s (parallel {mode})")


def _prefetch_turn_start(query):
    """
    Latency fix — start, in parallel, the model calls that _answer() would
    otherwise make one by one before it can write the answer: conversation
    title, intent, difficulty, domain filter, scenario detection and the
    past-conversation check. Each prompt is built by the same function the
    step itself uses, and only when that step could actually send it.
    _answer() still decides which results it uses, in the original order.
    Greetings and "thanks" messages need at most one call, so they are left
    alone.
    """
    q_lower = (query or "").strip().lower().rstrip("!?.")
    if is_satisfaction_signal(query) or q_lower in GREETINGS:
        return
    if "openai_llm" not in st.session_state:
        return

    prompts = []
    if st.session_state.get("new_conversation"):
        prompts.append(_title_prompt(query))

    key = (query or "").strip().lower()
    if key and key not in _get_intent_cache():
        prompts.append(_intent_prompt(query))

    # Difficulty: same early exits as detect_difficulty() and the caller.
    is_positive_statement = is_personal_experience_signal(query) and not is_struggle_signal(query)
    if not is_positive_statement and not is_struggle_signal(query):
        past_messages = st.session_state.conversation[-8:-1].copy() if len(st.session_state.conversation) > 1 else []
        if len(past_messages) >= 2:
            prompts.append(_difficulty_prompt(query, past_messages))

    # The scenario-selection path answers directly and skips the rest.
    if not st.session_state.get("selected_scenario"):
        active_scenario = st.session_state.get("active_scenario")
        has_prior_context = bool(st.session_state.get("last_substantive_query", ""))
        looks_like_followup = has_prior_context and (is_followup_query(query) or is_struggle_signal(query))
        if not active_scenario and not looks_like_followup:
            prompts.append(_domain_filter_prompt(query))
        if not active_scenario and not (is_urgent(query) or is_clearly_unambiguous(query)):
            prompts.append(_scenario_prompt(query))
        past = _past_conversation_messages()
        if past:
            prompts.append(_past_conversation_prompt(query, past))

    if len(prompts) > 1:
        llm_parallel.prefetch(prompts)


def _prefetch_after_answer(query, final_response, suggestion_intent, intent):
    """
    Latency fix — once the answer exists, the follow-up suggester and both
    Tier 2 checks (resolution, then session-end) only need the question and
    that answer, so they are started together. check_response_confidence()
    still only USES the session-end result when the resolution check passes,
    exactly as before.
    """
    prompts = []
    if suggestion_intent not in _SUGGESTER_SKIP_INTENTS:
        prompts.append(_suggester_prompt(query, final_response, suggestion_intent))
    current_turn = st.session_state.get("msg_count", 0)
    if st.session_state.get("survey_evaluated_turn") != current_turn and tier2_would_call_model(intent):
        prompts.append(_confidence_prompt(query, final_response, intent))
        prompts.append(_session_end_prompt())
    if len(prompts) > 1:
        llm_parallel.prefetch(prompts)


def _answer(query):

    with st.spinner("Thinking..."):
        if st.session_state.new_conversation:
            st.session_state.conversation_title = generate_conversation_title(query)

    # ══════════════════════════════════════════════════
    # TIER 1: SATISFACTION SIGNAL CHECK
    # ══════════════════════════════════════════════════
    with st.spinner("Understanding your question..."):
        is_satisfaction = detect_satisfaction_signal(query)

    # ── GREETING CHECK ──
    q_lower = (query or "").strip().lower().rstrip("!?.")
    if not is_satisfaction and q_lower in GREETINGS:
        # Fix (Oct 2026, found in cross-platform testing) — this used to
        # return a hardcoded "SC1003" greeting regardless of which course
        # the chatbot was deployed for. It now names the configured course.
        return f"Hello! How can I help you with {_course_label()} today?"

    if is_satisfaction:
        print("[Survey] Tier 1: Satisfaction signal — generating warm response")

        warm_response = generate_satisfaction_response(query)

        st.session_state.detected_intent_for_survey = "satisfaction"

        last_query = st.session_state.get("last_substantive_query", query)
        last_intent = st.session_state.get("last_substantive_intent", "other")
        print(f"[Survey] Tier 1 using stored query for bucket: '{last_query[:50]}...' (intent: {last_intent})")

        current_turn = st.session_state.get("msg_count", 0)
        if st.session_state.get("survey_evaluated_turn") != current_turn:
            st.session_state.survey_questions = find_questions_tier1(last_query, last_intent)
            st.session_state.survey_evaluated_turn = current_turn
        else:
            print("[Survey] Using cached survey result for this turn")

        st.session_state.follow_up_suggestions = []

        return warm_response

    # ── STEP 1: Detect intent ──
    with st.spinner("Understanding your question..."):
        intent_result = detect_intent(query)
        intent = intent_result.get("intent", "other")
        print(f"Detected intent: {intent} — {intent_result.get('reasoning', '')}")

    # ── STEP 2: Check difficulty — for response style adaptation only ──
    with st.spinner("Checking past conversations..."):
        # Fix #7 — shared is_personal_experience_signal() instead of a local
        # POSITIVE_SIGNALS list (which previously used "i love" as a substring,
        # incorrectly matching inside "i loved this course but I'm still
        # confused" and suppressing difficulty detection in the same message).
        is_positive_statement = is_personal_experience_signal(query) and not is_struggle_signal(query)

        if intent != "difficulty" and not is_positive_statement:
            is_struggling = detect_difficulty(query)
            if is_struggling:
                intent = "difficulty"
                print("Intent overridden to 'difficulty' based on struggle detection")
        elif is_positive_statement:
            print(f"[Survey] Positive statement detected — skipping difficulty override")

    # ── STEP 3: Handle scenario clarification ──
    selected_scenario = st.session_state.get("selected_scenario")
    if selected_scenario:
        print(f"Answering for selected scenario: {selected_scenario}")
        st.session_state.selected_scenario = None
        st.session_state.active_scenario = selected_scenario

        scenario_response = answer_with_scenario(query, selected_scenario, intent)

        try:
            original_query = st.session_state.get("pending_scenario_query") or query or ""
            original_intent = st.session_state.get("pending_scenario_intent") or intent or "other"
            original_query = str(original_query) if original_query else ""
            original_intent = str(original_intent) if original_intent else "other"
            if original_query:
                st.session_state["last_substantive_query"] = original_query
                st.session_state["last_substantive_intent"] = original_intent
                print(f"[Survey] Stored last substantive query (scenario): '{original_query[:50]}' (intent: {original_intent})")
            else:
                print(f"[Survey] No original query found for scenario — keeping existing stored query")
        except Exception as e:
            print(f"[Survey] Could not store scenario query: {e}")

        st.session_state.detected_intent_for_survey = intent
        current_turn = st.session_state.get("msg_count", 0)
        if st.session_state.get("survey_evaluated_turn") != current_turn:
            # Fix #2 — pass the answer that was just generated so question
            # selection can see what the student actually just experienced,
            # instead of evaluating against conversation history that's
            # missing this turn's response (it isn't written to
            # st.session_state.messages until after answer() returns).
            st.session_state.survey_questions = find_exact_matches_intersection(query, intent, scenario_response)
            st.session_state.survey_evaluated_turn = current_turn

        return scenario_response

    # ── OUT-OF-SCOPE CHECK ──
    # Fix #2 — previously bypassed entirely once any prior substantive query
    # existed, meaning an off-topic query later in the conversation could
    # slip through undetected. Now only bypasses when the current query
    # actually looks like a follow-up to the ongoing topic (shared
    # is_followup_query() from signals.py), not just because SOME prior
    # exchange happened.
    #
    # Patch (found via testing) — a struggle/difficulty phrase like
    # "I'm still confused" carries no topic words of its own; checked in
    # isolation it will almost always look off-topic to the scope filter,
    # even though it's clearly a continuation of whatever was just being
    # discussed. Struggle signals are now treated as an additional reason
    # to bypass, alongside genuine follow-up phrasing.
    has_prior_context = bool(st.session_state.get("last_substantive_query", ""))
    looks_like_followup = has_prior_context and (is_followup_query(query) or is_struggle_signal(query))
    if not st.session_state.get("active_scenario") and not looks_like_followup:
        with st.spinner("Checking relevance..."):
            if not is_relevant_to_context(query):
                print(f"[Response] Out of scope — rejecting before scenario detection")
                return f"My area of expertise is in {st.session_state.chatbot_context}. Ask related questions."

    # Skip scenario detection if active scenario context
    if not st.session_state.get("active_scenario"):
        with st.spinner("Understanding your question..."):
            scenario_result = derive_scenarios(query)
            if scenario_result["is_ambiguous"]:
                print(f"Ambiguous query detected — storing scenarios for UI")
                st.session_state.pending_scenarios = scenario_result["scenarios"]
                st.session_state.pending_scenario_query = query
                st.session_state.pending_scenario_intent = intent
                return None
    else:
        print(f"Active scenario context: {st.session_state.active_scenario} — skipping scenario detection")

    # ── STEP 4: Generate the actual answer ──
    suggestion_intent = "explanation" if st.session_state.get("active_scenario") else intent

    with st.spinner("Checking past conversations..."):
        is_relevant_to_past = if_related_to_past_conversations(query)
        print(is_relevant_to_past)

    if is_relevant_to_past["is_related"]:
        conversation_query = is_relevant_to_past["relevant_query"]
        conversation_history = is_relevant_to_past["relevant_response"]

        with st.spinner("Searching for relevant information..."):
            info_response = is_relevant_to_information(conversation_query, intent)

        if info_response["relevant"]:
            final_response = continue_from_previous_conversation(
                query, conversation_history, info_response["response"], intent
            )
        else:
            with st.spinner("Checking notes for relevant context..."):
                relevant_to_notes = is_relevant_to_notes(conversation_query, intent)

            if relevant_to_notes["relevant"]:
                with st.spinner("Formulating answer..."):
                    final_response = continue_from_previous_conversation(
                        query, conversation_history, relevant_to_notes["response"], intent
                    )
            else:
                if is_relevant_to_context(conversation_query):
                    with st.spinner("Formulating answer..."):
                        final_response = continue_from_previous_conversation(
                            query, conversation_history, "None", intent
                        )
                else:
                    return f"My area of expertise is in {st.session_state.chatbot_context}. Ask related questions."
    else:
        with st.spinner("Searching for relevant information..."):
            info_response = is_relevant_to_information(query, intent)
            print(info_response)

        if info_response["relevant"]:
            final_response = info_response["response"]
        else:
            with st.spinner("Checking notes for relevant context..."):
                relevant_to_notes = is_relevant_to_notes(query, intent)
                print(relevant_to_notes)

            if relevant_to_notes["relevant"]:
                with st.spinner("Formulating answer..."):
                    final_response = relevant_to_notes["response"]
            else:
                if is_relevant_to_context(query):
                    with st.spinner("Formulating answer..."):
                        final_response = generate_relevant_answer_with_links(query, "None", intent)
                else:
                    return f"My area of expertise is in {st.session_state.chatbot_context}. Ask related questions."

    # Every branch above that produced an answer called the follow-up
    # suggester on it as its last step; that call now happens once, here,
    # with the Tier 2 checks started alongside it (latency fix).
    _prefetch_after_answer(query, final_response, suggestion_intent, intent)
    final_response = suggest_follow_up_topics(query, final_response, suggestion_intent)

    # ══════════════════════════════════════════════════
    # TIER 2: CONFIDENCE-BASED TRIGGER
    # ══════════════════════════════════════════════════

    st.session_state["last_substantive_query"] = query
    st.session_state["last_substantive_intent"] = intent
    print(f"[Survey] Stored last substantive query: '{query[:50]}' (intent: {intent})")

    st.session_state.detected_intent_for_survey = intent
    current_turn = st.session_state.get("msg_count", 0)

    if st.session_state.get("survey_evaluated_turn") == current_turn:
        print("[Survey] Using cached survey result for this turn")
    else:
        with st.spinner("Thinking..."):
            confidence, resolved, reasoning = check_response_confidence(
                query, final_response, intent
            )

        if resolved:
            print(f"[Survey] Tier 2 fired — running survey pipeline")
            # Fix #2 — final_response threaded through so question selection
            # actually sees this turn's answer.
            st.session_state.survey_questions = find_questions_tier2(query, intent, final_response)
            st.session_state.survey_evaluated_turn = current_turn
        else:
            print(f"[Survey] Tier 2 did not fire — falling back to Tier 3")
            # Fix #1 — find_exact_matches_intersection() now itself checks
            # whether Tier 2 explicitly found THIS turn's concern unresolved
            # (as opposed to merely "not yet near session end") and, if so,
            # declines to override that finding — see the guard at the top
            # of that function in survey_gen.py.
            st.session_state.survey_questions = find_exact_matches_intersection(query, intent, final_response)
            st.session_state.survey_evaluated_turn = current_turn

    return final_response