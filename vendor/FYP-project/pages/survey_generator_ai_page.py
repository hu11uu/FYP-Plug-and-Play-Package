import streamlit as st
from helper.survey_generation_llm import *
from helper.survey_creation import *
import time
import json
from helper.survey_generation_llm import *

def generate_page():

    if "is_loading" not in st.session_state:
        st.session_state["is_loading"] = False
    if "processing_complete" not in st.session_state:
        st.session_state["processing_complete"] = False

    if st.button("⬅️ Back to Main Page", disabled = st.session_state["is_loading"]):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

    if "new_survey_questions" not in st.session_state:
        st.session_state["new_survey_questions"] = None
    if "topic_input" not in st.session_state:
        st.session_state["topic"] = ""
    if "purpose_input" not in st.session_state:
        st.session_state["purpose"] = ""
    if "audience_input" not in st.session_state:
        st.session_state["audience"] = ""

    # Check if questions have been generated
    if not st.session_state["is_loading"] and not st.session_state["processing_complete"] and st.session_state["new_survey_questions"] is None :

        st.title("🚀 Generate Survey Questions")
        st.markdown("Leverage on AI to help you create your ideal survey questions!")
        
        st.info("**Survey Topic:** Enter the main subject of your survey. Examples include: 'Employee Satisfaction' or 'Customer Feedback on Product X'.")
        topic = st.text_input("Enter the survey topic:", key="topic_input")
        st.info("**Survey Purpose:** Explain why you are conducting this survey. An example is: 'To understand employee engagement levels and identify areas for improvement.'")
        purpose = st.text_area("Describe the purpose of the survey:", key="purpose_input")
        st.info("**Target Audience:** Specify who will be answering the survey. Examples include: 'IT professionals working remotely' or 'Customers who purchased in the last 6 months'.")
        audience = st.text_input("Who is the target audience?", key="audience_input")

        if st.button("Generate Questions"):
            if topic and purpose and audience:
                st.session_state["topic"] = topic
                st.session_state["purpose"] = purpose
                st.session_state["audience"] = audience
                st.session_state["is_loading"] = True
                st.rerun()
            else:
                st.warning("Please fill in all fields.")
    
    elif st.session_state["is_loading"] and not st.session_state["processing_complete"]:
        time.sleep(0.5)
        st.title("Generating Your Survey Questions now, please hold on...")

        # Create a progress bar
        progress_placeholder = st.empty()
        status_text = st.empty()
        
        with progress_placeholder.container():
            progress_bar = st.progress(0)

        # Step 1    
        topic = st.session_state["topic"]
        purpose = st.session_state["purpose"]
        audience = st.session_state["audience"]
        progress_bar.progress(0)
        status_text.text("🧠 Generating questions based on topic, purpose and audience...")
        step1 = generate_questions_step1(topic, purpose, audience)
        progress_bar.progress(25)

        # Step 2
        status_text.text("🧠 Generating relevant tags to each question...")
        step2 = generate_questions_step2(step1)
        progress_bar.progress(50)

        # Step 3
        status_text.text("🧠 Finalizing survey questions...")
        step3 = generate_questions_step3(step2, purpose)
        progress_bar.progress(75)

        # Final step
        status_text.text("🧠 Outputting in correct format...")
        st.session_state["new_survey_questions"] = generate_json(step3, purpose)
        progress_bar.progress(100)

        # Update state
        st.session_state["is_loading"] = False
        st.session_state["processing_complete"] = True
        time.sleep(0.5)
        st.rerun() 

    elif st.session_state["processing_complete"] and st.session_state["new_survey_questions"] is not None:
        
        st.info(f"""Useful Tools:
                \n❌ Start Over: Reset and fill up the inputs again.
                \n➕ Add Question: Want to add a new question to the survey? Do so with this button.
                \n ✅ Next: Click this button if you are satisfied with the generated questions.
                """)
        
        col1, col2, col3 = st.columns([0.25, 0.3, 2.25])

        with col1:
            # Add a "Start Over" button to reset and show inputs again
            if st.button("❌ Start Over"):
                st.session_state["new_survey_questions"] = None
                st.rerun()

        with col2:
             if st.button("➕ Add Question"):
                st.session_state["show_add_question"] = True  # Set session state flag

        with col3:
            if st.button("✅ Next"):
                st.session_state["page"] = "confirmation"  # Navigate to confirmation page
                st.rerun()

        # NEW QUESTION CONTAINER
        if st.session_state.get("show_add_question", False):  
            st.subheader("➕ Add a New Question:")

            new_category = st.text_input("Enter Category for New Question:")
            new_question = st.text_area("Enter Content for New Question:")

            col_add, col_cancel = st.columns([0.15, 1])
            
            with col_add:
                if st.button("✅ Add New Question"):
                    if new_category and new_question:
                        # Ensure survey_json is correctly initialized
                        survey = st.session_state.get("new_survey_questions", '{"questions": []}')
                        cleaned_survey = survey.replace("```json", "").replace("```", "").strip()
                        survey_json = json.loads(cleaned_survey)

                        new_question_obj = {
                            "category": new_category,
                            "content": new_question
                        }
                        survey_json["questions"].append(new_question_obj)  # Add new question to the list
                        updated_survey_str = json.dumps(survey_json, indent=2)
                        st.session_state["new_survey_questions"] = updated_survey_str
                        st.session_state["show_add_question"] = False  # Hide input fields
                        st.toast("✅ New question added successfully!", icon="🎉")
                        st.rerun()  # Rerun to update UI
                    else:
                        st.error("Please provide both category and question content.")

            with col_cancel:
                if st.button("❌ Cancel"):
                    st.session_state["show_add_question"] = False  # Hide input fields
                    st.rerun()
        
        st.subheader("🚀 Generated Survey Questions:")
        survey = st.session_state["new_survey_questions"]
        cleaned_survey = survey.replace("```json", "").replace("```", "").strip()
        survey_json = json.loads(cleaned_survey)

        # Initialize edit state if not exists
        if "editing_question" not in st.session_state:
            st.session_state.editing_question = None
            
        # Display collapsible sections for each question with question number
        for idx, question in enumerate(survey_json["questions"], start=1):
            with st.expander(f"**Question {idx}**", expanded=True):
                # Check if this question is being edited
                is_editing = st.session_state.editing_question == idx
                
                if not is_editing:
                    # Regular display
                    st.write(f"**Question:** {question['question']}")
                    tags_text = ", ".join(question["tags"])
                    st.write(f"**Tags:** {tags_text}")

                    # Create columns for buttons
                    column1, column2, column3 = st.columns([0.47, 0.48, 1.5])  # Adjust width ratios if needed
                    
                    with column1:
                        # Button to regenerate with AI
                        if st.button(f"🔄 Regenerate Question {idx} with AI", key=f"regen_button_{idx}"):
                            response = regenerate_question(question, tags_text, survey_json)
                            cleaned_response = response.replace("```json", "").replace("```", "").strip()
                            regenerated_json = json.loads(cleaned_response)
                            # Update the question in the survey JSON
                            survey_json["questions"][idx - 1] = regenerated_json
                            updated_survey_str = json.dumps(survey_json, indent=2)
                            st.session_state["new_survey_questions"] = updated_survey_str
                            st.rerun()
                    
                    with column2:
                        # Button to switch to edit mode
                        if st.button(f"✏️ Edit Question/Tags {idx} manually", key=f"edit_button_{idx}"):
                            st.session_state.editing_question = idx
                            # Initialize edit values in session state
                            st.session_state[f"temp_question_{idx}"] = question["question"]
                            st.session_state[f"temp_tags_{idx}"] = tags_text
                            st.rerun()

                    with column3:
                        # Delete button
                        if st.button(f"❌ Delete Question {idx}", key=f"delete_button_{idx}"):
                            survey_json["questions"].pop(idx - 1)  # Remove the question from the list
                            updated_survey_str = json.dumps(survey_json, indent=2)
                            st.session_state["new_survey_questions"] = updated_survey_str
                            st.rerun()

                else:
                    # Edit mode
                    # Use session state to maintain values between reruns
                    st.session_state[f"temp_question_{idx}"] = st.text_input(
                        f"Edit Question {idx}",
                        value=st.session_state[f"temp_question_{idx}"],
                        key=f"edit_question_input_{idx}"
                    )

                    st.session_state[f"temp_tags_{idx}"] = st.text_input(
                        f"Edit Tags {idx}",
                        value=st.session_state[f"temp_tags_{idx}"],
                        key=f"edit_tags_input_{idx}"
                    )
                    
                    # Save button
                    if st.button(f"💾 Save Edits", key=f"save_edits_{idx}"):
                        survey_json["questions"][idx - 1]["question"] = st.session_state[f"temp_question_{idx}"]
                        # Ensure tags are stored as a list
                        edited_tags_input = st.session_state[f"temp_tags_{idx}"]
                        survey_json["questions"][idx - 1]["tags"] = [tag.strip() for tag in edited_tags_input.split(",")] if isinstance(edited_tags_input, str) else edited_tags_input
                        updated_survey_str = json.dumps(survey_json, indent=2)
                        st.session_state["new_survey_questions"] = updated_survey_str
                        st.session_state.editing_question = None  # Exit edit mode
                        st.rerun()
                    
                    # Cancel button
                    if st.button(f"❌ Cancel", key=f"cancel_edits_{idx}"):
                        st.session_state.editing_question = None  # Exit edit mode
                        st.rerun()

#Asks users to check 
def confirmation_page():

    if st.button("⬅️ Back to Main Page"):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

    st.title("Final Confirmation Of Survey Questions")
    st.info(f"""Useful Tools:
                \n⬅️ Back: Edit or regenerate questions with AI again.
                \n ✅ Next: Click this button if you confirm the generated questions.
                """)

    col1, col2 = st.columns([0.2, 2.8])

    with col1:
        if st.button("⬅️ Back", key="back_button"):
            st.session_state.page = "generate"  # Navigate to survey page
            st.rerun()

    with col2:
        if st.button("✅ Next", key="next_button"):
            st.session_state.page = "index_generation"  # Navigate to survey page
            st.rerun()

    #print(st.session_state["new_survey_questions"])

    # Clean the JSON string by removing triple backticks if they exist
    json_string = st.session_state["new_survey_questions"].strip("`json").strip("`")

    # Load JSON safely
    confirmed_survey_questions = json.loads(json_string)

    # Display questions in a clean format
    for idx, question in enumerate(confirmed_survey_questions.get("questions", []), 1):
        q_text = question.get("question", "")
        tags = question.get("tags", [])

        # Create styled output without background colors, with "Tags:" label
        st.markdown(f"""
        <div style='padding: 10px; 
                    border-left: 3px solid #4CAF50;
                    margin: 5px 0;
                    border-radius: 4px;'>
            <b>Question {idx}</b>: {q_text}
            {('<br><div style="margin-top: 8px;"><b>Tags:</b> ' + 
            ' '.join([f'<span style="padding: 3px 8px; border-radius: 12px; font-size: 0.9em; margin: 2px; border: 1px solid #4CAF50;">{tag}</span>' for tag in tags]) + 
            '</div>') if tags else ''}
        </div>
        """, unsafe_allow_html=True)



def index_generation_page():
    # Initialize states if needed
    if "processing_started" not in st.session_state:
        st.session_state.processing_started = False
    if "processing_completed" not in st.session_state:
        st.session_state.processing_completed = False
    if "index_completed" not in st.session_state:
        st.session_state.index_completed = False
    
    # Navigation button
    if st.button("⬅️ Back to Main Page"):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()
    
    # Show title and description
    st.title("Azure AI Search Index Generation")
    
    # Check if we need to process questions from llm_check_question
    if "llm_check_question" in st.session_state and not st.session_state.processing_started and not st.session_state.processing_completed:
        # Display progress indicators first
        st.write("Processing survey questions...")
        progress_placeholder = st.empty()
        status_text = st.empty()
        
        with progress_placeholder.container():
            progress_bar = st.progress(0)
            
        status_text.text("Generating tags for questions...")
        
        # Mark that we've started processing
        st.session_state.processing_started = True
        st.rerun()  # Rerun to render the UI before heavy processing
    
    # Continue processing in a separate state update
    elif st.session_state.processing_started and not st.session_state.processing_completed and "llm_check_question" in st.session_state:
        progress_bar = st.progress(30)
        status_text = st.empty()
        status_text.text("Processing questions earlier into a suitable indexing format...")
        
        # Do the heavy lifting
        step1 = generate_questions_step2(st.session_state.llm_check_question["valid_questions"])
        progress_bar.progress(60)
        
        status_text.text("Checking results...")
        if "new_survey_questions" not in st.session_state:
            st.session_state.new_survey_questions = None
            
        st.session_state.new_survey_questions = generate_json(step1, purpose=None)
        progress_bar.progress(100)
        
        # Mark processing as complete
        st.session_state.processing_completed = True
        st.session_state.processing_started = False
        
        time.sleep(0.5)  # Small delay to show completion
        st.rerun()
    
    # Main index generation UI after processing is complete
    elif not st.session_state.index_completed:
        st.write("Please wait while we generate an Azure AI Search Index for your survey questions.")
        
        # Check if we have questions to index
        if "new_survey_questions" not in st.session_state or st.session_state.new_survey_questions is None:
            st.warning("No survey questions available. Please generate or upload questions first.")
            return
            
        # Set running state for button
        if 'run_button' in st.session_state and st.session_state.run_button:
            st.session_state.running = True
        else:
            st.session_state.running = False
            
        if st.button('Start Index Generation', disabled=st.session_state.running, key='run_button'):
            progress_text = st.empty()
            progress_bar = st.progress(0)
            
            try:
                progress_text.write("Deleting old survey index...")
                delete_index_function("survey_questions")
                progress_bar.progress(20)
                
                progress_text.write("Inserting new survey index...")
                embedding_dimensions = load_embeddings()
                progress_bar.progress(40)
                
                create_index("survey_questions", embedding_dimensions)
                progress_bar.progress(60)
                
                progress_text.write("Populating new survey index with data...")
                progress_bar.progress(80)
                
                # Clean up JSON if needed (handle markdown code blocks)
                json_format = st.session_state["new_survey_questions"]
                if isinstance(json_format, str):
                    json_format = json_format.replace("```json", "").replace("```", "").strip()
                    questions_data = json.loads(json_format)
                else:
                    questions_data = st.session_state["new_survey_questions"]
                
                add_or_update_docs(questions_data, "survey_questions")
                progress_bar.progress(100)
                
                st.balloons()
                st.session_state.index_completed = True
                time.sleep(1)
                st.rerun()
                
            except Exception as e:
                st.error(f"An error occurred during index generation: {str(e)}")
    else:
        # Display completion message
        st.session_state["new_survey_questions"] = None
        st.success(
        f"""✨ Congratulations! ✨
        🎯 Your survey questions have been successfully indexed into Azure AI Search!

        What's been accomplished:
        📊 Survey questions processed and vectorized
        🔍 Search index created and optimized
        🤖 Chatbot is now ready to use your custom survey data

        Next steps:
        1. 🧪 Test the survey functionality with your chatbot
        2. 💬 Try asking questions to the chatbot and see what survey questions are prompted
        3. 📝 Answer the survey questions and navigate to the "Survey Visualisation" page to get insights!

        Need to make changes? You can:
        🔄 Re-run the whole process again anytime
        ➕ Add more survey questions
        🔧 Modify existing questions
        \n Happy surveying! 🎉"""
        )
    