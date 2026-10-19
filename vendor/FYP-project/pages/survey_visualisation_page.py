import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import json
import statistics
from collections import Counter

from helper.survey_gen import get_question_category_resolved

# Load environment variables
load_dotenv(override=True)

# ── QUESTION CATEGORY MAPPING ─────────────────────────────────────────────────
# Leftover #2 — this file used to maintain its own hand-typed copy of
# QUESTION_CATEGORIES (with capitalized keys) alongside a second, separately
# hand-typed copy in survey_gen.py (lowercase keys). The two could silently
# drift apart, which is exactly what caused the Fix #4 collision bug in the
# first place. There is now exactly ONE source of truth — the hardcoded dict
# in helper/signals.py, extended by survey_gen.py's get_question_category_resolved()
# with a Mongo-cached + LLM-classified fallback for any question that isn't
# in the hardcoded baseline (e.g. new questions added via the Survey
# Generator page) — instead of this dashboard silently defaulting unknown
# questions to "Core" with no way to reclassify them correctly.
#
# This page displays categories capitalized ("Teaching", "Assessment", ...)
# while the shared resolver returns lowercase ("teaching", "assessment", ...)
# to match the rest of the pipeline — this small map just bridges display
# formatting, it isn't a second copy of the category data itself.
_CATEGORY_DISPLAY_MAP = {
    "teaching": "Teaching",
    "assessment": "Assessment",
    "engagement": "Engagement",
    "core": "Core",
    "general": "Core",  # matches this page's previous fallback behaviour
}


def get_question_category(question_text: str) -> str:
    """
    Delegates to the shared resolver in survey_gen.py instead of maintaining
    a separate, duplicated category dict here. See _CATEGORY_DISPLAY_MAP
    above for why the result is capitalized before being returned.
    """
    resolved = get_question_category_resolved(question_text)
    return _CATEGORY_DISPLAY_MAP.get(resolved, "Core")


CATEGORY_COLORS = {
    "Teaching": "#4C9BE8",
    "Assessment": "#E8834C",
    "Engagement": "#4CE8A0",
    "Core": "#B44CE8"
}

DIVERGING_COLORS = {
    1: "#d62728",      # Red — Very Poor
    2: "#f4a261",      # Light Red/Orange
    3: "#adb5bd",      # Gray — Neutral
    4: "#74c476",      # Light Green
    5: "#238b45",      # Dark Green — Very Good
}


@st.cache_resource(show_spinner=False)
def get_db_connection():
    return MongoClient(os.environ['PYMONGO_CONNECTION_STRING'])


@st.cache_data(ttl=600, show_spinner="Loading survey data...")
def get_survey_data():
    client = get_db_connection()
    return list(client['chatbot']['surveys'].find({}))


@st.cache_data(ttl=600, show_spinner=False)
def generate_ai_summary(
    total_surveys: int,
    category_avgs: dict,
    category_counts: dict,
    top_questions: list,
    bottom_questions: list,
    overall_avg: float,
    overall_spread: float,
    rating_distribution: dict,
) -> str:
    """
    Generate an AI summary paragraph for the dashboard.

    Fix (Leftover #7) — two changes from the original version:

    1. CACHING — this previously re-ran (and re-billed an API call) on every
       single page refresh, even when the underlying data hadn't changed at
       all. Now wrapped in @st.cache_data, same pattern already used for
       get_survey_data(). Deliberately takes plain, pre-computed summary
       values (ints/floats/dicts/lists of strings) as arguments rather than
       the raw survey_data list — that list contains MongoDB ObjectId
       values per document, which Streamlit's cache hasher may not handle
       cleanly. Passing only the derived numbers this function actually
       needs avoids that risk entirely, and gives a smaller, more stable
       cache key besides.

    2. CONTENT — the original prompt only ever saw category-level averages
       and one top/bottom question's text (no scores, no sample sizes, no
       distribution shape). That's why it kept producing bland, repetitive
       output ("overall sentiment was mixed... Core received highest...").
       This version feeds the model genuinely richer material — per-category
       sample sizes, the actual lowest-scoring individual question with its
       real number, the overall rating spread, and the full 1-5 rating
       distribution — so it has something concrete to reason over instead
       of restating two numbers in prose.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

        category_detail = ", ".join([
            f"{cat} ({avg:.1f}/5, n={category_counts.get(cat, 0)})"
            for cat, avg in category_avgs.items()
        ])
        top_q = top_questions[0]['question'][:80] if top_questions else "N/A"
        top_score = top_questions[0]['avg'] if top_questions else None
        bot_q = bottom_questions[0]['question'][:80] if bottom_questions else "N/A"
        bot_score = bottom_questions[0]['avg'] if bottom_questions else None

        distribution_str = ", ".join([
            f"{rating}★: {rating_distribution.get(rating, 0)}"
            for rating in [1, 2, 3, 4, 5]
        ])

        category_spread = (max(category_avgs.values()) - min(category_avgs.values())) if category_avgs else 0

        prompt = f"""You are analyzing student feedback data for an SC1003 Teaching Assistant Chatbot dashboard.

DATA:
- Total survey sessions: {total_surveys}
- Overall average rating: {overall_avg:.1f}/5 (standard deviation: {overall_spread:.2f})
- Category averages with sample sizes: {category_detail}
- Spread between best and worst category: {category_spread:.2f} points
- Highest-rated individual question: "{top_q}" — {top_score:.1f}/5
- Lowest-rated individual question: "{bot_q}" — {bot_score:.1f}/5
- Full rating distribution across all responses: {distribution_str}

Write a summary for a professor reviewing this dashboard, in 4-5 dense sentences —
concrete and specific, but not padded with filler. Every sentence should carry a
real piece of information, not restate the previous one differently.

RULES — follow these precisely:
- Lead with the single most important, concrete finding — not a restatement of the raw numbers.
- If the category spread is small (under ~0.3 points) but individual questions still
  range widely (compare the top and bottom individual scores, not just category
  averages), say BOTH: categories look consistent on average, but specific items still
  vary meaningfully — that's a more precise and more useful read than either fact alone.
- Reference the SPECIFIC lowest-rated individual question by what it's actually
  asking, and name which category it falls under — that's what's actually
  actionable, a category label alone isn't.
- Look at the rating distribution: if it's polarized (roughly similar counts at both
  the low and high end, few in the middle) call that out explicitly — it means
  "split opinion" rather than "moderate satisfaction," a materially different read.
- If any category has a noticeably small sample size compared to the others, briefly
  flag that as a caveat on how much weight to put on that category's average.
- End with ONE genuinely concrete, specific next step tied directly to what the
  lowest-rated question is actually asking — a real practice a TA could try, not a
  restatement like "focus on improving X." For example, if the lowest question is
  about checking understanding before moving on, a concrete suggestion looks like
  "adding a one-question recap before advancing to the next topic" — specific
  enough that someone could act on it tomorrow, not just know the general area.
- Confident, professional tone. No hedging, no filler phrases ("it is worth noting",
  "overall"), no bullet points, no jargon.

Keep the whole thing under 110 words, but make every word earn its place — dense
with real information, not padded to hit a length."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        # Fallback — generate summary without LLM. Upgraded (per feedback
        # that the original fallback read too generically) to include: the
        # category of the lowest-rated item (not just its text), the actual
        # gap between the single best and single worst individual question
        # (which can be much larger than the category-level spread — both
        # facts are true at once and worth stating together, not just one),
        # and a category-aware concrete suggestion instead of a generic
        # "focus on improving X" line. Can't invent a suggestion tied to the
        # exact question's semantic content without an LLM call, but a
        # suggestion tied to its CATEGORY is genuinely more specific than a
        # bare category name, and is grounded in real data, not invented.
        CATEGORY_ACTION_HINTS = {
            "Teaching": "reviewing pacing or adding a quick comprehension check before moving to the next topic",
            "Assessment": "clarifying task instructions and expectations earlier in the interaction",
            "Engagement": "building in more open-ended or discussion-style prompts",
            "Core": "reviewing response clarity and how approachable the interaction feels",
        }

        if category_avgs:
            best_cat = max(category_avgs, key=category_avgs.get)
            worst_cat = min(category_avgs, key=category_avgs.get)
            category_spread = max(category_avgs.values()) - min(category_avgs.values())

            top_q = top_questions[0]['question'][:70] if top_questions else None
            top_score = top_questions[0]['avg'] if top_questions else None
            bot_q = bottom_questions[0]['question'][:70] if bottom_questions else None
            bot_score = bottom_questions[0]['avg'] if bottom_questions else None
            bot_cat = bottom_questions[0]['category'] if bottom_questions else None

            item_spread = (top_score - bot_score) if (top_score is not None and bot_score is not None) else 0

            if category_spread < 0.3 and item_spread >= 0.8:
                spread_note = (
                    f"Category averages are tightly clustered (within {category_spread:.1f} points), "
                    f"but individual questions still range from {top_score:.1f} to {bot_score:.1f}/5 — "
                    f"consistency at the category level is masking real variation between specific items."
                )
            elif category_spread < 0.3:
                spread_note = "Ratings are tightly clustered across categories, suggesting consistent experience overall."
            else:
                spread_note = f"{best_cat} led at {category_avgs[best_cat]:.1f}/5, while {worst_cat} trailed at {category_avgs[worst_cat]:.1f}/5."

            if bot_q:
                action_hint = CATEGORY_ACTION_HINTS.get(bot_cat, "reviewing this specific area more closely")
                action_line = (
                    f' The lowest-rated item — "{bot_q}" ({bot_score:.1f}/5, {bot_cat}) — '
                    f'points toward {action_hint} as the most concrete next step.'
                )
            else:
                action_line = ""

            return (
                f"Across {total_surveys} survey sessions, the overall average rating was {overall_avg:.1f}/5. "
                f"{spread_note}{action_line}"
            )
        return "Insufficient data to generate summary."


# ── PAGE LAYOUT ───────────────────────────────────────────────────────────────
st.title("Survey Results Dashboard")
st.divider()

survey_data = get_survey_data()

if not survey_data:
    st.warning("No survey data available!")
else:
    # ── PROCESS DATA ──────────────────────────────────────────────────────────
    all_questions = list(set([
        q for doc in survey_data
        if 'questions' in doc
        for q in doc['questions']
    ]))
    sorted_questions = sorted(all_questions)

    # Fix (found via testing, Day 3) — get_question_category() was being
    # called repeatedly for the same question across this page: 4 times in
    # the category-averages loop alone (once per category, since the loop
    # re-scans every question for each of the 4 categories), then again for
    # top/bottom questions, again in the completeness table, and again in
    # the detailed breakdown. For a brand-new/uncached question, several of
    # these calls could each miss the Mongo cache (which only gets written
    # AFTER the first classification completes) and independently trigger
    # their own redundant LLM classification call — confirmed live: one
    # question was sent to the LLM classifier 3 times in a single page load.
    #
    # Computing every question's category ONCE here, into a plain dict, and
    # having every section below do a dict lookup instead of calling
    # get_question_category() again fixes this at the source — regardless
    # of how many places on the page need a question's category, the
    # actual resolution (hardcoded dict -> Mongo cache -> LLM) now only
    # happens once per question per page load.
    question_categories_map = {q: get_question_category(q) for q in sorted_questions}

    # Build user responses dict
    user_responses = {}
    for doc in survey_data:
        if 'user' not in doc or 'questions' not in doc or 'answers' not in doc:
            continue
        user = doc['user']
        if user not in user_responses:
            user_responses[user] = {}
        for q, a in zip(doc['questions'], doc['answers']):
            user_responses[user][q] = a

    # Build per-question answer data
    question_data = {}
    for question in sorted_questions:
        answers = []
        for doc in survey_data:
            if 'questions' in doc and question in doc['questions']:
                idx = doc['questions'].index(question)
                answers.append(doc['answers'][idx])
        question_data[question] = answers

    # Compute average rating per question
    question_avgs = {
        q: sum(a) / len(a) for q, a in question_data.items() if a
    }

    # Compute category averages
    category_avgs = {}
    category_counts = {}  # Fix #7 — needed for AI summary sample-size caveats
    for cat in ["Teaching", "Assessment", "Engagement", "Core"]:
        cat_questions = [
            q for q in sorted_questions
            if question_categories_map[q] == cat
        ]
        cat_ratings = []
        for q in cat_questions:
            cat_ratings.extend(question_data.get(q, []))
        if cat_ratings:
            category_avgs[cat] = sum(cat_ratings) / len(cat_ratings)
            category_counts[cat] = len(cat_ratings)

    # Compute overall average
    all_ratings = [a for answers in question_data.values() for a in answers]
    overall_avg = sum(all_ratings) / len(all_ratings) if all_ratings else 0
    # Fix #7 — spread and full distribution, so the AI summary can reason
    # about clustering/polarization instead of only ever seeing an average.
    overall_spread = statistics.pstdev(all_ratings) if len(all_ratings) > 1 else 0
    rating_distribution = dict(Counter(all_ratings))

    # Top 3 and Bottom 3 questions
    # Fix (Leftover #9) — with very few distinct scored questions, the same
    # question could appear in both Top 3 and Bottom 3 (e.g. with only 4
    # scored questions, the 3rd-best and 3rd-worst overlap). Not observed in
    # practice with real data (34 questions, comfortably above the ~6
    # needed to avoid this), but this guard makes it impossible instead of
    # just unlikely — cheap insurance for future scenarios like filtering
    # by date range or module, where the scored pool could shrink.
    sorted_by_avg = sorted(question_avgs.items(), key=lambda x: x[1], reverse=True)
    MIN_QUESTIONS_FOR_TOP_BOTTOM_SPLIT = 6
    top_questions = [
        {'question': q, 'avg': avg, 'category': question_categories_map[q]}
        for q, avg in sorted_by_avg[:3]
    ]
    if len(sorted_by_avg) >= MIN_QUESTIONS_FOR_TOP_BOTTOM_SPLIT:
        bottom_questions = [
            {'question': q, 'avg': avg, 'category': question_categories_map[q]}
            for q, avg in sorted_by_avg[-3:][::-1]
        ]
    else:
        # Not enough distinct questions to guarantee Top 3 and Bottom 3
        # don't share entries — show fewer bottom items rather than risk
        # displaying the same question as both a strength and a weakness.
        bottom_questions = [
            {'question': q, 'avg': avg, 'category': question_categories_map[q]}
            for q, avg in sorted_by_avg[len(top_questions):][::-1]
        ]

    # ── CHANGE 1: AI SUMMARY ──────────────────────────────────────────────────
    st.subheader("📝 AI Summary")
    with st.spinner("Generating AI summary..."):
        summary = generate_ai_summary(
            total_surveys=len(survey_data),
            category_avgs=category_avgs,
            category_counts=category_counts,
            top_questions=top_questions,
            bottom_questions=bottom_questions,
            overall_avg=overall_avg,
            overall_spread=overall_spread,
            rating_distribution=rating_distribution,
        )
    st.info(summary)
    st.divider()

    # ── CHANGE 2: SUMMARY STATISTICS (Fixed) ─────────────────────────────────
    st.subheader("📊 Summary Statistics")
    # New (per feedback) — "Surveys Completed" counts SESSIONS, but each
    # session can include 2-3 individual question ratings (per the Tier 1/2/3
    # selection logic), so it understates how much actual feedback data has
    # been collected. Total Responses = total individual ratings given
    # across all sessions — a more accurate sense of data volume for a
    # professor deciding how much weight to put on the dashboard's numbers.
    total_responses = sum(len(doc.get('answers', [])) for doc in survey_data)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Surveys Completed", len(survey_data))
    with col2:
        st.metric("Total Responses", total_responses)
    with col3:
        st.metric("Average Rating", f"{overall_avg:.1f} / 5")
    st.divider()

    # ── CHANGE 3: CATEGORY AVERAGE RATING BAR CHART ───────────────────────────
    st.subheader("📂 Average Rating by Category")
    st.caption("How students rated the teaching assistant across each feedback dimension.")

    if category_avgs:
        cat_df = pd.DataFrame([
            {'Category': cat, 'Average Rating': avg, 'Color': CATEGORY_COLORS.get(cat, '#888')}
            for cat, avg in category_avgs.items()
        ]).sort_values('Average Rating', ascending=True)

        fig_cat = go.Figure()
        for _, row in cat_df.iterrows():
            fig_cat.add_trace(go.Bar(
                x=[row['Average Rating']],
                y=[row['Category']],
                orientation='h',
                marker_color=row['Color'],
                text=[f"{row['Average Rating']:.1f} / 5"],
                textposition='outside',
                name=row['Category'],
                showlegend=False,
            ))

        fig_cat.add_vline(
            x=3.0, line_dash="dash", line_color="gray",
            annotation_text="Neutral (3.0)", annotation_position="top"
        )

        fig_cat.update_layout(
            xaxis=dict(range=[0, 5.5], title="Average Rating (out of 5)"),
            yaxis=dict(title=""),
            height=280,
            margin=dict(l=20, r=60, t=20, b=40),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
        )
        st.plotly_chart(fig_cat, width="stretch")
    st.divider()

    # ── CHANGE 4: TOP 3 STRENGTHS / BOTTOM 3 AREAS ────────────────────────────
    st.subheader("🏆 Strengths & Areas for Improvement")
    col_top, col_bot = st.columns(2)

    with col_top:
        st.markdown("**✅ Top 3 Strengths**")
        for i, item in enumerate(top_questions, 1):
            cat_color = CATEGORY_COLORS.get(item['category'], '#888')
            st.markdown(
                f"{i}. <span style='color:{cat_color};font-weight:600'>[{item['category']}]</span> "
                f"{item['question'][:70]}{'...' if len(item['question']) > 70 else ''} "
                f"— **{item['avg']:.1f}/5**",
                unsafe_allow_html=True
            )

    with col_bot:
        st.markdown("**⚠️ Bottom 3 Areas to Improve**")
        for i, item in enumerate(bottom_questions, 1):
            cat_color = CATEGORY_COLORS.get(item['category'], '#888')
            st.markdown(
                f"{i}. <span style='color:{cat_color};font-weight:600'>[{item['category']}]</span> "
                f"{item['question'][:70]}{'...' if len(item['question']) > 70 else ''} "
                f"— **{item['avg']:.1f}/5**",
                unsafe_allow_html=True
            )
    st.divider()

    # ── INDIVIDUAL USER COMPLETENESS ──────────────────────────────────────────
    st.subheader("📋 Individual User Completeness")
    st.caption("✔️ = answered, ❌ = not yet answered")

    data = []
    for question in sorted_questions:
        cat = question_categories_map[question]
        row = {'Category': cat, 'Question': question}
        for user in user_responses:
            row[user] = '✔️' if question in user_responses[user] else '❌'
        data.append(row)

    df_raw = pd.DataFrame(data)
    df_raw = df_raw[['Category', 'Question'] + list(user_responses.keys())]
    styled_df = df_raw.style.map(
        lambda x: 'color: #d62728' if x == '❌' else ('color: #2ca02c' if x == '✔️' else ''),
        subset=list(user_responses.keys())
    )
    st.dataframe(
        styled_df,
        use_container_width=True,  # will update after Streamlit deprecation
        column_config={
            "Category": st.column_config.TextColumn("Category", width="small"),
            "Question": st.column_config.TextColumn("Survey Question", width="large"),
            **{user: st.column_config.Column(user, width="small") for user in user_responses}
        }
    )
    st.divider()

    # ── SURVEY SUBMISSION TIMELINE ────────────────────────────────────────────
    st.subheader("🕒 Survey Submission Timeline")
    st.caption("Interactive view of response patterns over time.")

    timeline_data = []
    for doc in survey_data:
        if 'timestamp' in doc:
            try:
                ts = pd.to_datetime(doc['timestamp'])
                timeline_data.append({
                    'User': doc['user'],
                    'Timestamp': ts
                })
            except (TypeError, ValueError):
                pass

    if not timeline_data:
        st.warning("No timestamp data available!")
    else:
        df_timeline = pd.DataFrame(timeline_data)
        fig_tl = px.scatter(
            df_timeline,
            x='Timestamp',
            y='User',
            color='User',
            labels={'Timestamp': 'Submission Time', 'User': 'Respondent'},
            category_orders={"User": sorted(df_timeline['User'].unique())},
            color_discrete_sequence=px.colors.qualitative.Plotly
        )
        fig_tl.update_traces(
            marker=dict(size=10, line=dict(width=2, color='DarkSlateGrey')),
            hovertemplate="<b>%{y}</b><br>%{x|%Y-%m-%d %H:%M:%S}<extra></extra>"
        )
        fig_tl.update_layout(
            xaxis=dict(
                rangeselector=dict(buttons=[
                    dict(count=1, label="1d", step="day", stepmode="backward"),
                    dict(count=7, label="1w", step="day", stepmode="backward"),
                    dict(count=1, label="1m", step="month", stepmode="backward"),
                    dict(step="all")
                ]),
                rangeslider=dict(visible=True),
                type="date"
            ),
            height=400,
            showlegend=False
        )
        st.plotly_chart(fig_tl, width="stretch")
    st.divider()

    # ── CHANGE 5: DIVERGING COLOUR SCALE ─────────────────────────────────────
    st.subheader("📈 Answer Distribution Overview")
    st.caption("Stacked rating breakdown per question. Hover for detailed counts.")

    dist_data = []
    for question in sorted_questions:
        answers = question_data[question]
        answer_counts = pd.Series(answers).value_counts().reindex(range(1, 6), fill_value=0)
        dist_data.append({
            'Question': question,
            'Short Question': question[:45] + '...' if len(question) > 45 else question,
            **{f'Rating {i}': answer_counts[i] for i in range(1, 6)}
        })
    df_dist = pd.DataFrame(dist_data)

    fig_dist = go.Figure()

    rating_labels = {
        1: "Very Poor",
        2: "Poor",
        3: "Neutral",
        4: "Good",
        5: "Very Good"
    }

    for rating in range(5, 0, -1):
        fig_dist.add_trace(go.Bar(
            y=df_dist['Short Question'],
            x=df_dist[f'Rating {rating}'],
            name=f"Rating {rating} — {rating_labels[rating]}",
            orientation='h',
            marker_color=DIVERGING_COLORS[rating],
            text=df_dist[f'Rating {rating}'].apply(lambda x: str(x) if x > 0 else ''),
            textposition='inside',
        ))

    # Invisible hover trace
    hover_texts = []
    for idx, row in df_dist.iterrows():
        hover = f"<b>{row['Question']}</b><br>"
        for r in range(1, 6):
            hover += f"{rating_labels[r]}: {row[f'Rating {r}']}<br>"
        hover_texts.append(hover)

    fig_dist.add_trace(go.Scatter(
        x=[0] * len(df_dist),
        y=df_dist['Short Question'],
        mode='markers',
        marker=dict(size=0, opacity=0),
        hoverinfo='text',
        hovertext=hover_texts,
        showlegend=False
    ))

    fig_dist.update_layout(
        barmode='stack',
        height=500 + len(df_dist) * 22,
        showlegend=True,
        xaxis_title="Number of Responses",
        yaxis_title="",
        hovermode='y unified',
        hoverlabel=dict(bgcolor="#0E1117", font_color="#FAFAFA", font_size=12),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )
    st.plotly_chart(fig_dist, width="stretch")
    st.divider()

    # ── DETAILED QUESTION ANALYSIS ────────────────────────────────────────────
    st.subheader("🔍 Detailed Question Analysis")
    st.caption("Click each question to expand detailed response metrics.")

    for question in sorted_questions:
        answers = question_data[question]
        cat = question_categories_map[question]
        cat_color = CATEGORY_COLORS.get(cat, '#888')
        avg = question_avgs.get(question, 0)

        label = f"[{cat}] {question[:70]}{'...' if len(question) > 70 else ''} — avg {avg:.1f}/5"

        with st.expander(label, expanded=False):
            if not answers:
                st.write("No responses for this question")
                continue

            tab1, tab2 = st.tabs(["Distribution", "Breakdown"])

            with tab1:
                fig1 = go.Figure()
                for r in range(1, 6):
                    count = sum(1 for a in answers if a == r)
                    fig1.add_trace(go.Bar(
                        x=[f"Rating {r}\n{rating_labels[r]}"],
                        y=[count],
                        name=rating_labels[r],
                        marker_color=DIVERGING_COLORS[r],
                        showlegend=False,
                        text=[count],
                        textposition='outside'
                    ))
                fig1.update_layout(
                    title="Response Distribution",
                    xaxis_title="Rating",
                    yaxis_title="Count",
                    height=300,
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                )
                st.plotly_chart(fig1, width="stretch", key=f"dist_{question}")

            with tab2:
                fig2 = px.pie(
                    names=[f"Rating {r} — {rating_labels[r]}" for r in pd.Series(answers).value_counts().index],
                    values=pd.Series(answers).value_counts().values,
                    hole=0.3,
                    title="Response Breakdown",
                    color_discrete_sequence=[DIVERGING_COLORS[r] for r in pd.Series(answers).value_counts().index]
                )
                fig2.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig2, width="stretch", key=f"pie_{question}")