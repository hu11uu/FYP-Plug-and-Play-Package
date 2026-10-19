import streamlit as st

def generate_questions_step1(topic, purpose, audience):

    prompt_1 = f"""
    # Survey Question Generation Task

    ## Context
    You are designing a survey with the following details:
    - **Topic:** {topic}
    - **Purpose:** {purpose}
    - **Target Audience:** {audience}

    ## Task Overview
    Two agents will collaborate to generate a well-structured Likert scale survey:

    ### **Agent 1: Survey Design Expert**
    Your task is to analyze the given context and provide recommendations for structuring the survey.

    - **Optimal Number of Questions:** Suggest a suitable number of questions based on best practices.
    - **Key Dimensions:** Identify the essential themes or aspects the survey should cover to achieve its purpose.

    ### **Agent 2: Content Specialist**
    Using the recommendations from Agent 1, generate Likert scale questions.

    - **Ensure Comprehensive Coverage:** Each identified dimension must have at least one question.
    - **Craft Clear and Neutral Questions:**
    - Use concise and unambiguous language.
    - Avoid biased, leading, or double-barreled questions.
    - Ensure appropriateness for the target audience.
    - **Maintain Proper Formatting:**
    - **Question Text**: Clearly stated.

    ## Output Format:
    Return a structured list of survey questions, aligned with the original survey purpose and audience.
    """

    return st.session_state.openai_llm.invoke(prompt_1).content

def generate_questions_step2(response_1):

    prompt_tags= f"""

    # Tag Generation Task

    ## Context
    A structured list of survey questions: {response_1} has been provided. Your task is to analyze each question and extract relevant **tags** that capture its core themes.
    If there is a section on invalid questions, do not ignore it.

    ## Instructions

    ### **Your Role: Tag Generation Expert**
    You specialize in identifying key themes from survey questions. For each question, extract a set of concise, relevant tags that reflect the main ideas, ensuring that they:

    - **Capture the Essence:** Tags should summarize the key aspects of the question.
    - **Remain Concise:** Each tag should be a short, descriptive phrase (1-3 words).
    - **Be Non-Redundant:** Avoid repeating similar terms.
    - **Follow a Consistent Format:** Use lowercase and separate multiple-word tags with spaces (e.g., "student engagement", "teaching effectiveness").
    - **Cover Multiple Perspectives:** Include aspects such as different perspectives, roles, or contexts where relevant.

    ## Input Format:
    A structured list of survey questions.

    ## **Example Output Format**
    **Question:** This faculty member encouraged engagement in the course; as a result of the teaching approaches taken by this faculty member I was involved and interested in the course.  
    **Tags:** student engagement, teaching approach, interactivity, classroom dynamics, motivation, learning environment, student participation

    **Question:** This faculty member communicated clearly: this faculty member was easy to understand in all forms of communication including in classes, online, and in writing.  
    **Tags:** communication skills, clarity, understanding, teaching effectiveness, online communication, written communication, verbal communication

    """

    return st.session_state.openai_llm.invoke(prompt_tags).content

def generate_questions_step3(tags, purpose=None):  # Make purpose optional with default value
    prompt_2 = f"""
    ### Agent 3: Quality Control Reviewer

    You have received the following draft of survey questions along with its tags{
        ' and the purpose of the survey' if purpose else ''
    }:

    {tags}

    {purpose if purpose else ''}  # Only show purpose if provided

    Perform a final review to ensure the survey is well-structured and ready for deployment.

    ### Your Task:
    1. Questions should strictly use Likert scale format (1-5 agreement scale)
    2. Check for Redundancy: Identify and remove any duplicate or overlapping questions
    3. Ensure Logical Flow: Organize questions in a natural progression
    4. Verify Alignment: {
        f"Confirm all questions relate to: {purpose}" if purpose else "Ensure question relevance"
    }
    5. Refine Language: Use clear, simple phrasing appropriate for all respondents

    ### Final Output:
    Provide only the finalized Likert-scale questions with their tags, formatted properly.
    """

    return st.session_state.openai_llm.invoke(prompt_2).content

def generate_json(final_output, purpose=None):

    json_prompt = f"""
    Convert this survey data to JSON: {final_output}
    
    Required structure:
    {{
        "questions": [
            {{
                "question": "text",
                "tags": ["tag1", "tag2"]
            }},
            ...
        ]
    }}
    
    Rules:
    1. Include only the JSON output
    2. Preserve all original tags
    3. Maintain question order
    {
        f"4. Ensure questions align with: {purpose}" if purpose else ""
    }

    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    json_response = st.session_state.openai_llm.invoke(json_prompt).content
    return json_response

def regenerate_question(question, tags, survey):
    prompt = f"""
    # Survey Question Generation Task

    ## Context
    A user wants to regenerate a survey question:
    - **Original Question:** {question}
    - **Tags:** {tags}
    - **All survey questions:** {survey}

    ## Instructions
    - Your task is to use the contexts from the original question, tags, and all survey questions to **generate a completely new question** that is:
      - Distinct from the original question and existing survey questions
      - Not a rewording/rephrasing of the original
      - Focused on different aspects/angles of the topic
      - Strictly Likert scale format (not open-ended)
    - **Generate new tags** that:
        - Align with the **new question** you generate.
        - Tags should be **concise**, **contextually relevant**, and **cover the core themes** of the new question.
        - Use **2 to 5 tags** per question, ensuring they capture key aspects of the question's focus.
        - Avoid using the same tags as the original question unless they remain highly relevant.
        - If the question shifts focus to a related but distinct aspect, adjust the tags accordingly.
    
    ### Your Task:
    Create a JSON object with:
        - 'question': Unique Likert-scale question
        - 'tags': A list of relevant tags (2-5) based on the newly generated question.

    Important: Ensure your response is valid JSON that can be parsed programmatically.
    """

    response = st.session_state.openai_llm.invoke(prompt).content

    return response