import streamlit as st
from PyPDF2 import PdfReader
import docx
from io import StringIO
import json
from pages.survey_generator_ai_page import index_generation_page

def get_file_type(uploaded_file):
    """Determine file type from extension or MIME type"""
    filename = uploaded_file.name.lower()
    mime_type = uploaded_file.type.lower()
    
    if filename.endswith('.pdf') or 'pdf' in mime_type:
        return 'PDF'
    elif filename.endswith('.docx') or 'word' in mime_type:
        return 'Word'
    elif filename.endswith('.json') or 'json' in mime_type:
        return 'JSON'
    elif filename.endswith('.txt') or 'text/plain' in mime_type:
        return 'Text'
    else:
        return 'Unknown'

def process_file(uploaded_file):
    with st.spinner("Validating your file, please wait..."):
        # Store uploaded file in session state
        if "processed_uploaded_file" not in st.session_state:
            st.session_state["processed_uploaded_file"] = None

        """Process file based on its type"""
        file_type = get_file_type(uploaded_file)
        
        if file_type == 'PDF':
            # Add PDF processing function
            st.session_state["processed_uploaded_file"]=process_pdf_file()
            
        elif file_type == 'Word':
            # Add Word document processing function
            st.session_state["processed_uploaded_file"]=process_word_file()
            
        elif file_type == 'JSON':
            # Add JSON processing function
            st.session_state["processed_uploaded_file"]=process_json_file()
            
        elif file_type == 'Text':
            # Add text file processing function
            st.session_state["processed_uploaded_file"]=process_text_file()
            
        else:
            st.error("Unsupported file format, please go back and try again")

def valid_survey_question_checker(text):
    prompt=f"""
    Content: {text}

    **Task:** Analyze the user-provided content and check whether they are legitimate survey questions or not.
    If they are valid survey questions, respond with "Yes". Otherwise, respond with "No". Strictly follow the format "Yes" or "No".
    """
    response = st.session_state.openai_llm.invoke(prompt).content
    print(response)
    return response

def process_pdf_file():
    # Read PDF file
    pdf_reader = PdfReader(st.session_state["uploaded_file"])
    text = "\n".join([page.extract_text() for page in pdf_reader.pages if page.extract_text()])
    valid_question_response=valid_survey_question_checker(text)

    if valid_question_response=="No":
        st.error("The uploaded file contains invalid survey questions, please reupload the file and try again.")
    else:
        st.write("📑 Extracted PDF Text:")
        st.success(text if text else "No text found in the PDF.")
        return text


def process_word_file():
    # Read Word document
    doc = docx.Document(st.session_state["uploaded_file"])
    text = "\n".join([para.text for para in doc.paragraphs])
    valid_question_response=valid_survey_question_checker(text)

    if valid_question_response=="No":
        st.error("The uploaded file contains invalid survey questions, please go back and try again")
    else:
        st.write("📜 Extracted Word Document Text:")
        st.success(text if text else "No text found in the document.")
        return text

def process_json_file():
    # Read the content of the file into a string
    file_content = st.session_state["uploaded_file"].getvalue().decode("utf-8")
    json_data = json.loads(file_content)
    processed_json_data = json.dumps(json_data, indent=2)
    valid_question_response=valid_survey_question_checker(processed_json_data)

    if valid_question_response=="No":
        st.error("The uploaded file contains invalid survey questions, please go back and try again")
    else:
        formatted_json_data = f"```json\n{processed_json_data}\n```"
        st.write("📂 JSON File Content:")
        st.success(formatted_json_data)
        return processed_json_data

def process_text_file():
    # Read text file
    stringio = StringIO(st.session_state["uploaded_file"].getvalue().decode("utf-8"))
    string_data = stringio.read()
    valid_question_response=valid_survey_question_checker(string_data)

    if valid_question_response=="No":
        st.error("The uploaded file contains invalid survey questions, please go back and try again")
    else:
        st.write("📄 File Content:")
        st.success(string_data)
        return string_data

def upload_processing_page():
    if st.button("⬅️ Back to Main Page"):
        st.session_state["page"] = "main"
        keys_to_keep = {"username", "authenticated"}
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

    st.title("Check your uploaded file:")

    st.info("""Note: Extracted document may not appear 100% similar to the uploaded file because it is AI-generated. 
            Do not worry as this will be handled by further AI processing later on.""")

    col1, col2 = st.columns([0.15,1.25])

    with col1:
        if st.button("❌ Reupload files", key="reupload_1"):
            st.session_state["page"] = "upload"
            st.rerun()

    with col2:
        if st.button("✅ Next", key="next_button_1"):
            st.session_state["page"] = "ai_processing_staging"
            st.rerun()

    process_file(st.session_state["uploaded_file"])

def ai_processing_staging_page():

    if st.button("⬅️ Back to Main Page"):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

        
    st.title("AI check on your uploaded files")

    col1, col2 = st.columns([0.15,1.25])

    with col1:
        if st.button("❌ Reupload files", key="reupload_2"):
            st.session_state["page"] = "upload"
            st.rerun()

    with col2:
        if st.button("✅ Start AI checker"):
            st.session_state["page"] = "ai_processing_check"
            st.rerun()

    st.info("""
    **🤖 What is this?**\n
    Before indexing, your survey questions will undergo AI processing to ensure quality and consistency:

    **What the AI checks for:**
    - Proper Likert scale format
    - Grammar and spelling accuracy
    - Question clarity and readability
    - Duplicate or similar questions
    - Similar domain focus

    **Benefits:**
    - Ensures consistent question format
    - Improves data quality
    - Reduces survey bias
    - Optimizes question effectiveness

    This process helps create a more reliable and effective survey experience. Processing time varies based on file size.
    """)

def validate_questions_step1(raw_questions, progress_bar, status_text):

    status_text.text("🧠 Validating Likert scale compatibility...")
    progress_bar.progress(0)

    """Initial validation: Likert scale compatibility"""
    step1_prompt = f"""
    Perform INITIAL validation of these survey questions:
    {raw_questions}
    1. For each question, determine if it can be reasonably answered using a 1-5 Likert scale

    2. Only flag questions that are CLEARLY incompatible with Likert scales:
    - Questions explicitly requiring binary yes/no answers (e.g., "Did you enjoy the workshop?")
    - Questions specifically asking to choose from multiple categories (e.g., "Which menu item did you prefer?")
    - Questions explicitly requesting free-text responses (e.g., "Please describe your experience.")
    - Questions with multiple embedded prompts requiring different response formats

    3. Statements of opinion or assessment that a respondent can agree or disagree with ARE valid for Likert scales, even if they could hypothetically be answered yes/no

    4. Give questions the benefit of doubt and be leniant - if a question could potentially work with a Likert scale, consider it valid

    Return JSON format:
    {{
        "valid_questions": [],
        "invalid_questions": [{{"question": "...", "reason": "..."}}]
    }}
    """
    return st.session_state.openai_llm.invoke(step1_prompt).content

def validate_questions_step2(step1_output, progress_bar, status_text):

    status_text.text("🧠 Validating category consistency of questions...")
    progress_bar.progress(25)

    """Secondary check: Category consistency and question focus"""
    step2_prompt = f"""
    Analyze these pre-validated questions: 
    {step1_output}
    1. Verify questions are broadly related to a similar assessment domain
    2. Flag questions only if they:
    - Clearly belong to an entirely different evaluation category
    - Use drastically inconsistent perspectives that would confuse respondents
    - Contain evaluation criteria so ambiguous they cannot be meaningfully answered
    3. Be lenient - slight variations in focus or perspective are acceptable
    Maintain previous validity status where possible.
    Update JSON structure with new findings.
    """
    return st.session_state.openai_llm.invoke(step2_prompt).content

def validate_questions_step3(step2_output, progress_bar, status_text):
    
    status_text.text("🧠 Validating formatting and special cases handling...")
    progress_bar.progress(50)

    """Final formatting and special cases handling"""
    step3_prompt = f"""
    Process final validation adjustments:
    {step2_output}
    1. Do not preserve question codes (e.g., "D4:" or "Q5:" etc) if applicable
    2. For questions mentioning "scale of 1-5":
    - Keep verbatim - these are almost always valid
    3. Fix only severe grammar issues that impede understanding
    4. Err on the side of keeping questions valid - only flag questions with major incompatibilities
    5. Ensure final output format:
    {{
        "valid_questions": [],
        "invalid_questions": [{{"question": "...", "reason": "..."}}]
    }}
    """
    return st.session_state.openai_llm.invoke(step3_prompt).content

def repeat_question_checker(llm_response, progress_bar, status_text):
    
    status_text.text("🧠 Checking there is no repeated questions...")
    progress_bar.progress(75) 
    repeat_question_prompt = f"""
        
        **Question Repeat Check:**
        - DO NOT include any invalid questions in the `valid_questions` list
        - Ensure that each question appears ONLY ONCE in the output
        - If a question is flagged as invalid, it MUST NOT appear in the `valid_questions` list

        **Important Notes:**
        1. If a question is invalid, it MUST ONLY appear in the `invalid_questions` list.
        2. If a question is valid, it MUST ONLY appear in the `valid_questions` list.
        3. If there are no invalid questions, leave the `invalid_questions` list empty.
        4. ALWAYS ensure NO DUPLICATES exist between the `valid_questions` and `invalid_questions` lists.

        Questions to check: {llm_response}

        **Output Format:** (Strictly follow this format, with no additional text or explanations)
        {{
            "valid_questions": [
                "Question 1 text",
                "Question 2 text",
                ...
            ],
            "invalid_questions": [
                {{"question": "Bad question text", "reason": "Explanation..."}},
                ...
            ]
        }}
        """
        
    return st.session_state.openai_llm.invoke(repeat_question_prompt).content

def suggested_ai_fixes(invalid_questions):
    # Generate fixes for all invalid questions
    fix_prompt = f"""
    Act as a survey question editor specializing in Likert scale assessment items. 
    For each invalid question below, generate a corrected version that meets these requirements:

    1. If there is a question code, maintain the original question code (e.g., "D4:") at the beginning exactly as it appears
    2. Fixes the specific issue identified in the reason
    3. Format as a statement that can be rated on a 1-5 Likert scale (strongly disagree to strongly agree)
    4. Preserves the core meaning and assessment aspect of the original question, DO NOT CHANGE THE QUESTION DRASTICALLY

    Return ONLY a JSON array with corrected questions in their original order.

    Example Input: 
    ["D4: I would highly recommend the person. (Reason: This question is a statement that would be better suited for a binary yes/no answer.)]

    Example Output:
    ["D4: How much would recommend the person?"]

    Invalid Questions to Fix:
    {[f"{q['question']} (Reason: {q['reason']})" for q in invalid_questions]}
    """

    fix_response = st.session_state.openai_llm.invoke(fix_prompt).content
    return fix_response

def llm_file_check_page():

    if st.button("⬅️ Back to Main Page"):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

    st.title("AI checked your uploaded files and here are the results:")

    if st.session_state["processed_uploaded_file"] is None:
        st.error("An error occured. Please upload your files again.")
    
    else:
        if "llm_check_question" not in st.session_state:
            progress_bar = st.progress(0)
            status_text = st.empty()
            st.session_state["llm_check_question"] = None
            st.session_state["ai_fixes_applied"] = False
            step1 = validate_questions_step1(st.session_state.processed_uploaded_file, progress_bar, status_text)
            step2 = validate_questions_step2(step1, progress_bar, status_text)
            step3 = validate_questions_step3(step2, progress_bar, status_text)
            llm_response = repeat_question_checker(step3, progress_bar, status_text)
            progress_bar.empty()
            status_text.empty()
            parsed_llm_response = llm_response.replace("```json", "").replace("```", "").strip()
            #print("LLM Response:",llm_response)
            parsed_response = json.loads(parsed_llm_response)
            st.session_state["llm_check_question"] = parsed_response
            if "new_survey_questions" not in st.session_state:
                st.session_state["new_survey_questions"] = llm_response
            
        if st.session_state["llm_check_question"] is not None:

            st.info(f"""Useful Tools:
                \n ❌ Reupload files: Reupload the survey questions.
                \n ✅ Next: Click this button if there are no errors flagged by the AI and you confirm the generated questions. \n
                
                Note: If you disagree with the AI's suggestions or believe the flagged issues are not critical, you can still proceed. 
                """)

            col1, col2 = st.columns([0.15,1.25])

            with col1:
                if st.button("❌ Reupload files", key="reupload_3"):
                    st.session_state["page"] = "upload"
                    st.session_state["processed_uploaded_file"] = None
                    del st.session_state["llm_check_question"]
                    st.rerun()

            with col2:
                #if "llm_check_question" in st.session_state and not st.session_state.llm_check_question["invalid_questions"]:
                    if st.button("✅ Next", key="next_button_2"):
                        st.session_state["page"] = "index_generation"
                        st.rerun()

            questions = st.session_state["llm_check_question"]
            invalid_questions = questions.get("invalid_questions", [])
            
            if invalid_questions:
                st.subheader("📝 🚩 Action Required: Flagged Questions Detected")
                with st.expander("❓ Need Help?"):
                    st.error("""
                    ### How to edit invalid questions?

                    #### 1. Review Flagged Questions
                    Each question shows its specific validation error

                    #### 2. Choose Your Fix Method

                    ##### 🪄 Quick Fix (Recommended)
                    Click the **"🤖 Use AI Suggested Fix"** button to:
                    - Automatically correct all invalid questions
                    - Auto correct Likert scale compatibility if any

                    ##### ✏️ Manual Edit
                    If preferred, directly edit any question in the text boxes below

                    #### 3. Finalize Changes
                    After reviewing corrections:
                    - Click **"🔄 Revalidate All Questions"** to confirm fixes
                    - Repeat process if any new errors appear

                    ---

                    #### ❓ Example Fixes
                    | Original | Fixed |
                    |----------|-------|
                    | *"Do they provide good lectures?"* | *"This instructor delivers clear and engaging lectures"* |
                    | *"Preferred meeting time?"* | *"On a scale of 1-5, the scheduled meeting times accommodate my availability"* |
                    """)

                if "edited_invalid" not in st.session_state:
                    st.session_state["edited_invalid"] = [
                        q.copy() for q in invalid_questions
                    ]

                # Display editable fields for each invalid question
                for idx, item in enumerate(st.session_state["edited_invalid"]):
                    col1 = st.columns(1)[0]
                    with col1:
                        # Editable question field
                        st.markdown(f"##### Question {idx+1}: (Reason: {item['reason']})")
                        st.session_state.edited_invalid[idx]["question"] = st.text_input(
                            "Edit:",
                            value=item["question"],
                            key=f"invalid_edit_{idx}"
                        )
                        
                # Revalidation controls
                col1, col2, col3, col4 = st.columns([0.7, 0.65, 0.5, 2.6])
                with col1:
                    if st.button("🔄 Revalidate All Questions"):
                        # Combine valid + edited invalid questions
                        all_questions = (
                            questions["valid_questions"] +
                            [item["question"] for item in st.session_state["edited_invalid"]]
                        )

                        #st.success(all_questions)
                        
                        # Update the source data and clear previous results
                        st.session_state["processed_uploaded_file"] = all_questions
                        del st.session_state["llm_check_question"]
                        del st.session_state["edited_invalid"]
                        st.rerun()

                with col2:
                    if st.button("🤖 Use AI Suggested Fix", disabled=st.session_state.get("ai_fixes_applied", False)):
                        fix_response = suggested_ai_fixes(invalid_questions)
                        print(fix_response)

                        try:
                            # Clean response and parse
                            fix_response = fix_response.replace("```json", "").replace("```", "").strip()
                            ai_fixes = json.loads(fix_response)
                            
                            # Update edited_invalid with AI fixes
                            for idx, fixed_q in enumerate(ai_fixes):
                                if idx < len(st.session_state["edited_invalid"]):
                                    st.session_state.edited_invalid[idx]["question"] = fixed_q
                            
                            st.session_state["ai_fixes_applied"] = True  # Flag to disable button
                            st.rerun()

                        except Exception as e:
                            st.error(f"Failed to process AI fixes: {str(e)}")

                with col3:
                    if st.button("❌ Cancel Edits"):
                        del st.session_state["edited_invalid"]
                        st.rerun()

    # Display final valid questions after editing
    if "llm_check_question" in st.session_state and not st.session_state.llm_check_question["invalid_questions"]:

        st.success("✅ All questions validated successfully!")
        
        st.subheader("Final Approved Questions")
        
        # Create a clean numbered list with proper formatting
        for idx, question in enumerate(st.session_state.llm_check_question["valid_questions"], 1):
            # Extract question code if exists (like "O1:", "D4:")
            parts = question.split(":", 1)
            if len(parts) > 1:
                code = parts[0] + ":"
                text = parts[1].strip()
            else:
                code = ""
                text = question
            
            # Display with clean formatting
            st.markdown(f"""
            <div style='padding: 10px; border-left: 3px solid #4CAF50; margin: 5px 0;'>
                <b>Question {idx}</b>{' (' + code + ')' if code else ''}<br>
                {text}
            </div>
            """, unsafe_allow_html=True)

        
