import re

# ─────────────────────────────────────────────────────────────────────────────
# SHARED SIGNAL CONSTANTS — single source of truth
#
# Fix #7: previously these keyword lists existed in 4+ slightly different
# copies across response.py and survey_gen.py (struggle_signals in
# query_search_intelligent, a differently-worded struggle_signals_pipeline in
# run_survey_pipeline, another inline copy in _is_repeated_topic_pattern, plus
# separate copies of satisfaction/question-indicator lists in response.py and
# again inside find_exact_matches_intersection). This meant the system could
# disagree with itself about whether a student was struggling/satisfied
# depending on which function was asking.
#
# Everything downstream should import from here instead of hand-typing lists.
# Also the shared source for survey question categories (QUESTION_CATEGORIES
# + get_question_category()) — see that section further below for details.
# ─────────────────────────────────────────────────────────────────────────────

# ── SATISFACTION ──────────────────────────────────────────────────────────────
# Phrases that signal the student is satisfied and the topic is concluded.
SATISFACTION_PHRASES = [
    "i understand now", "i get it now", "that makes sense",
    "oh i see", "got it", "okay thanks", "ok thanks",
    "thank you", "thanks", "that helped", "that's clear now",
    "now i understand", "ah i see", "makes sense now",
    "perfect thanks", "i see now", "that clarifies it",
    "alright thanks", "oh okay i get it", "got it thanks",
    "i understand", "i get it", "that's clear", "clear now",
    "oh i get it", "ah i get it", "ah okay", "i see",
    "okay i get it", "ok i get it", "now i get it",
    "makes sense", "that makes more sense", "oh that makes sense",
    "i finally get it", "now i understand it", "thanks that helped",
    "oh okay thanks", "okay got it", "ok got it"
]

# If these appear alongside a satisfaction phrase, treat the message as a new
# question rather than a satisfaction signal.
QUESTION_INDICATORS = [
    "but", "however", "what about", "how about", "can you",
    "could you", "what is", "how does", "why does", "what are",
    "how do", "can i", "will", "would", "should", "?", "what if"
]

# ── STRUGGLE / DIFFICULTY ─────────────────────────────────────────────────────
# Union of every struggle-phrase variant previously scattered across the
# codebase (STRUGGLE_PHRASES, _DIFFICULTY_PHRASES, struggle_signals,
# struggle_signals_pipeline, and the inline list in _is_repeated_topic_pattern).
STRUGGLE_PHRASES = [
    "don't understand", "dont understand", "i don't understand", "i dont understand",
    "i'm confused", "im confused",
    "still don't get", "still dont get",
    "still don't understand", "still dont understand",
    "i still don't", "i still dont",
    "not getting it", "can you explain again", "explain again",
    "what do you mean", "i'm lost", "im lost",
    "can you simplify", "too complicated", "too complex", "makes no sense",
    "doesn't make sense", "doesnt make sense", "huh?", "huh", "what?", "why though",
    "i don't get it", "i dont get it", "don't get it", "dont get it",
    "can you repeat", "say that again",
    "not clear", "unclear", "confusing", "i'm still confused", "still confused",
    "help me understand", "struggling with", "having trouble",
    "can't understand", "cant understand", "not clear to me",
    "i'm struggling", "im struggling"
]

# ── DISSATISFACTION ───────────────────────────────────────────────────────────
# Union of dissatisfaction_signals + dissatisfaction_signals_pipeline.
DISSATISFACTION_PHRASES = [
    "not helpful", "didn't help", "didnt help", "didn't answer",
    "that's not what i meant", "that's wrong", "incorrect", "not what i asked",
    "not satisfied", "still confused", "that doesn't help",
    "preferred", "different way", "prefer a different", "would have preferred",
    "i would prefer", "not clear",
    "too complex", "too complicated", "too long", "too short",
    "not clear enough", "confusing explanation", "bad explanation",
    "not useful", "unhelpful", "doesn't make sense"
]

# ── PERSONAL EXPERIENCE / ENGAGEMENT ─────────────────────────────────────────
# Union of personal_experience_signals + _PERSONAL_REFLECTION_PHRASES + the
# separate POSITIVE_SIGNALS list from response.py's answer().
PERSONAL_EXPERIENCE_PHRASES = [
    "i found", "i found this", "i enjoyed", "i really enjoyed", "i liked",
    "i love", "i felt", "this challenged me", "really challenged",
    "i learned", "i learned a lot", "i appreciated", "i appreciate",
    "this helped me", "this helped", "i discovered",
    "i realized", "i realised", "changed how i think",
    "never realised", "i never realised", "never realized", "i never realized",
    "didn't realise", "didn't realize", "didn't know", "i didn't know",
    "didn't expect", "i didn't expect", "didn't think", "i didn't think",
    "i never thought", "i realise now", "i realize now",
    "really enjoyed", "enjoyed learning", "loved learning",
    "made the topic feel", "feel more relevant", "more relevant to me",
    "made it feel", "this is relevant", "i can relate",
    "makes sense to me now", "really interesting", "found it interesting",
    "found this interesting", "this resonated", "eye opening",
    "this was eye opening", "mind opening", "blew my mind", "this blew my mind",
    "fascinating", "captivating",
    "love how", "love this", "i love this", "i love how", "appreciate how",
    "changed how i", "this changed", "this really changed",
    "made me think", "got me thinking", "this made me",
    "opened my eyes", "surprised me", "amazed me",
    "this really", "this actually", "wow this",
    "great course", "very interesting", "this is good", "this was good", "this is great",
    "i can relate", "this is relevant", "i see the connection", "i now understand",
    "i feel more", "this helped me see", "this made sense"
]

# ── FOLLOW-UP REQUESTS ───────────────────────────────────────────────────────
# Phrases that clearly indicate a continuation of the current topic, not a
# new/ambiguous/out-of-scope query.
FOLLOW_UP_PHRASES = [
    "can you show me another", "show me another", "give me another example",
    "can you give me", "can you explain more", "tell me more",
    "what about", "how about", "and what about", "elaborate", "explain further"
]

# ── SCOPE / ASSESSMENT ───────────────────────────────────────────────────────
ASSESSMENT_KEYWORDS = [
    "quiz", "test", "exam", "grade", "mark", "submit", "submission",
    "deadline", "assignment", "when is", "how do i submit", "is the quiz",
    "what topics", "open book", "closed book", "weightage", "rubric",
    "how is it graded", "how many marks", "pass", "fail"
]

URGENT_KEYWORDS = [
    "urgent", "asap", "immediately", "deadline today", "due today",
    "due tomorrow", "tonight", "right now", "emergency", "help me now",
    "running out of time", "last minute", "panic"
]

GREETINGS = {
    "hello", "hi", "hey", "good morning", "good afternoon",
    "good evening", "thanks", "thank you", "ok", "okay",
    "bye", "goodbye", "yes", "no", "sure", "alright"
}

CLEAR_QUERY_STARTS = [
    "what is ", "what's ", "define ", "who is ", "when is ",
    "explain ", "how does ", "why does ", "difference between",
    "compare ", " vs ", "what are ", "how do ", "why is ",
    "can you show ", "show me ", "give me an example",
    "how do i ", "how can i ", "what does ",
    "do i need ", "do i have to ", "do we need ", "do we have to ",
    "is it ", "is there ", "are there ", "will there ",
    "can i ", "can we ", "should i ", "should we ",
    "where do i ", "where can i ", "when do i ", "when can i ",
    "who do i ", "which ", "how many ", "how much ",
    "am i ", "are we ", "does it ", "do they "
]

# ── QUESTION CATEGORY MAPPING ─────────────────────────────────────────────────
# Leftover item 2 — consolidated here as the single source of truth. Previously
# this dict existed as two separate, hand-typed copies (survey_gen.py and
# survey_visualisation_page.py) that could silently drift apart — which is
# exactly what caused the Fix #4 collision bug. Both files now import from
# here instead of keeping their own copy.
#
# This hardcoded dict only covers the known baseline set of questions. New
# questions added later are NOT meant to be added here by hand — see
# survey_gen.py's get_question_category_resolved() for the automatic
# Mongo-cached + LLM-classified fallback that handles anything not listed
# below, so this file never needs manual updates going forward.
QUESTION_CATEGORIES = {
    "teaching": [
        "the way the teaching assistant conducted interactions increased my understanding of the course",
        "the teaching assistant often illustrated or explained the subject matter with real-world examples that gave more meaning to the material",
        "the teaching assistant stressed the most important parts of the course so that i could see how the material fitted together",
        "the teaching assistant used strategies to check my understanding before moving on to new material",
        "the teaching assistant increased my interest in the course",
        "the teaching assistant presented the subject matter in a methodical and logical way",
        "the teaching assistant encouraged the development of practical skills",
        "the teaching assistant encouraged me to think deeply and analytically about the knowledge and concepts in the course",
        "the teaching assistant demonstrates enthusiasm for teaching"
    ],
    "assessment": [
        "the teaching assistant gave useful information on assessment, such as clear instructions and guidance on how to approach tasks",
        "the teaching assistant set thought-provoking questions that challenged my thinking",
        "the teaching assistant clearly explained the requirements of assessment tasks to enable me to understand the purpose of the task",
        "the teaching assistant provided responses that enabled me to demonstrate my achievement of the intended learning outcomes"
    ],
    "engagement": [
        "the teaching assistant challenged me to think deeply about the concepts",
        "the teaching assistant challenged my creativity",
        "the teaching assistant challenged me to engage in discussion and exploration of ideas",
        "the teaching assistant challenged me to find my own solutions to problems",
        "the teaching assistant challenged me to consider different perspectives in the subject area",
        "the teaching assistant challenged me to reflect on my thinking to formulate a new way of seeing things"
    ],
    "core": [
        # Fix #4 — "the teaching assistant increased my interest in the course"
        # removed from here; it already lives in "teaching" above and was
        # unreachable as a "core" question due to first-match resolution.
        "the teaching assistant encouraged engagement in the course; as a result of the interactions i was involved and interested in the course",
        "the teaching assistant communicated clearly and was easy to understand in all forms of communication",
        "the teaching assistant was approachable and created opportunities for students to ask questions and seek help",
        "the teaching assistant helped students understand important concepts by explaining how the course content fits together with principles and concepts",
        "the teaching assistant encouraged critical thinking; the interactions encouraged me to think deeply and analytically about the knowledge and concepts in the course",
        "the teaching assistant provided timely feedback that helped my progress and gave useful comments that assisted my learning",
        "the teaching assistant takes innovative approaches to student learning",
        "the teaching assistant as an online learning resource helped my learning",
        "the teaching assistant is well integrated with the course",
        "the teaching assistant is an effective tool for student learning in this course",
        "the teaching assistant helped me understand what i was expected to learn in this course",
        "the teaching assistant made effective use of collaborative and interactive learning approaches",
        "the teaching assistant encouraged learning through participation and interaction",
        "the teaching assistant communicated enthusiasm for the subject",
        "the teaching assistant indicated how the topics and theories in the course are related to other areas of the degree programme"
    ]
}


def get_question_category(question_text: str) -> str:
    """
    Fast, pure, dependency-free lookup against the hardcoded baseline dict
    above. Returns "general" if not found here — callers that want the
    smarter Mongo-cached + LLM-classified fallback for genuinely new
    questions should use survey_gen.py's get_question_category_resolved()
    instead, which calls this function first before falling back further.
    """
    q_lower = question_text.strip().lower()
    q_text = q_lower.split(":", 1)[-1].strip() if ":" in q_lower else q_lower
    for category, questions in QUESTION_CATEGORIES.items():
        for mapped_q in questions:
            if q_text == mapped_q or mapped_q in q_text or q_text in mapped_q:
                return category
    return "general"


# Pronouns that, combined with a short message, usually indicate a follow-up
# referring back to the previous exchange rather than a fresh/unrelated topic.
_REFERENTIAL_PRONOUNS = ["it", "that", "this", "those", "them", "these"]


# ── SHARED DETECTION HELPERS ─────────────────────────────────────────────────

def contains_any(text: str, phrases: list) -> bool:
    """Case-insensitive substring check against a phrase list."""
    if not text:
        return False
    text_lower = text.lower()
    return any(p in text_lower for p in phrases)


# Fix #3 — a broader set of new-request signals than QUESTION_INDICATORS
# alone. QUESTION_INDICATORS only catches "but"/"however"/"what about"/a
# literal "?" etc. — it misses plain imperative continuations like
# "Thanks. Explain recursion next" (no "but", no "?"). This regex looks for
# a verb that asks the chatbot to do something, anywhere in the message.
_NEW_REQUEST_VERBS = re.compile(
    r"\b(explain|show|tell|describe|walk me|give me|help me|teach me|"
    r"go over|cover|discuss|clarify|elaborate|demonstrate)\b"
)


def has_new_request(query: str) -> bool:
    """
    True if the message tacks on a new ask alongside (or instead of) a
    satisfaction phrase — broader than QUESTION_INDICATORS, which is
    limited to a small fixed phrase list and a literal "?".
    """
    if not query:
        return False
    q = query.lower().strip()
    if contains_any(q, QUESTION_INDICATORS):
        return True
    if _NEW_REQUEST_VERBS.search(q):
        return True
    # A short trailing continuation word like "next"/"now"/"also" after a
    # satisfaction phrase usually signals "and now do this other thing",
    # e.g. "Thanks, explain recursion next".
    if re.search(r"\b(next|also|now)\b", q) and contains_any(q, SATISFACTION_PHRASES):
        return True
    return False


# Fix #3 — words whose presence directly negates a satisfaction reading,
# even when a satisfaction phrase also appears in the same message (e.g.
# "I understand nothing" contains no listed STRUGGLE_PHRASES verbatim, but
# is obviously not a satisfaction signal).
_NEGATION_PATTERN = re.compile(
    r"\b(not|never|nothing|don't|dont|can't|cant|doesn't|didn't|doesnt|didnt)\b"
)


def is_satisfaction_signal(query: str) -> bool:
    """
    True only if the message is a satisfaction phrase, contains no struggle/
    dissatisfaction/negation signal, and tacks on no new request.

    Fix #3 — previously this checked satisfaction phrases in isolation, so
    a message that was simultaneously a struggle/dissatisfaction signal
    (e.g. "Thanks, I still don't understand" — which is ALSO a literal
    entry in STRUGGLE_PHRASES) was classified as pure satisfaction, because
    the satisfaction check ran alone and never cross-checked struggle.
    Similarly "Thanks. Explain recursion next" carries no "but"/"what
    about"/"?" (QUESTION_INDICATORS), so it slipped through as satisfaction
    even though a new request follows. This version:
      1. Rejects immediately if a struggle or dissatisfaction signal is
         present anywhere in the message — those take priority over any
         satisfaction phrase found alongside them.
      2. Rejects on an explicit negation word ("nothing", "don't", "can't",
         etc.) — catches phrasing not captured verbatim by STRUGGLE_PHRASES.
      3. Rejects if has_new_request() finds a new ask tacked on, using the
         broader detection above (not just QUESTION_INDICATORS).
    """
    if not query:
        return False
    query_lower = query.lower().strip().replace("\u2019", "'")

    if is_struggle_signal(query_lower) or is_dissatisfaction_signal(query_lower):
        return False

    if _NEGATION_PATTERN.search(query_lower):
        return False

    if not contains_any(query_lower, SATISFACTION_PHRASES):
        return False

    if has_new_request(query_lower):
        return False

    return True


def is_struggle_signal(text: str) -> bool:
    return contains_any(text, STRUGGLE_PHRASES)


def is_dissatisfaction_signal(text: str) -> bool:
    return contains_any(text, DISSATISFACTION_PHRASES)


def is_personal_experience_signal(text: str) -> bool:
    return contains_any(text, PERSONAL_EXPERIENCE_PHRASES)


def is_followup_query(query: str) -> bool:
    """
    True if the query looks like a continuation of the current topic rather
    than a fresh, standalone question — used to narrow the out-of-scope-check
    bypass (Fix #2) instead of blanket-skipping for the rest of the session.
    """
    if not query:
        return False
    q = query.strip().lower()
    if contains_any(q, FOLLOW_UP_PHRASES):
        return True
    # Short message containing a referential pronoun ("what about it?",
    # "explain that more") strongly suggests a follow-up, not a new topic.
    word_count = len(q.split())
    if word_count <= 6 and any(f" {p} " in f" {q} " or q.startswith(p + " ") for p in _REFERENTIAL_PRONOUNS):
        return True
    return False


def is_urgent(query: str) -> bool:
    return contains_any(query, URGENT_KEYWORDS)


# Leftover #1 (scenario over-triggering) — deliberately NOT patched with more
# hardcoded phrases here (e.g. "does this course...", "is the mini-project...").
# A hardcoded list can only ever catch phrasings someone thought to test, and
# playing whack-a-mole with wording was exactly the failure mode Fix #1 (Day 1)
# already rejected for CS_KEYWORDS. The real, generalizing fix lives in the
# LLM prompt inside derive_scenarios() in response.py, which was tightened
# with explicit criteria distinguishing "invites elaboration" from
# "genuinely needs a different answer" — that catches any phrasing, not just
# ones added here by hand.
def is_clearly_unambiguous(query: str) -> bool:
    q = (query or "").strip().lower()
    if q in GREETINGS or q.rstrip("!?.") in GREETINGS:
        return True
    if is_struggle_signal(q):
        return True
    if contains_any(q, PERSONAL_EXPERIENCE_PHRASES):
        return True
    if contains_any(q, FOLLOW_UP_PHRASES):
        return True
    return any(q.startswith(s) for s in CLEAR_QUERY_STARTS)