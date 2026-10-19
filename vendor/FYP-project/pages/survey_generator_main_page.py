import streamlit as st
from langchain_openai import AzureChatOpenAI
import os
from pages.survey_generator_ai_page import *
from pages.survey_generator_upload_page import *

# Set page configuration
st.set_page_config(layout="wide")

if 'openai_llm' not in st.session_state:
    st.session_state.openai_llm = AzureChatOpenAI(
        azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'],
        api_key=os.environ['AZURE_OPENAI_APIKEY'],
        deployment_name=os.environ['AZURE_OPENAI_DEPLOYMENT_NAME'],
        model_name=os.environ['AZURE_OPENAI_MODEL_NAME'],
        api_version=os.environ['AZURE_OPENAI_API_VERSION'],
        temperature=0
    )

# Main page
def main_page():
    st.title("🛠️ Survey Generator")
    st.markdown(
        "Welcome to the **Survey Generator** tool! You have two options to create survey questions:"
    )
    st.markdown("✅ **Upload your own files** containing pre-defined survey questions.")
    st.markdown("✅ **Generate survey questions** with the help of an **AI chatbot**.")

    # Create two equal-height columns
    col1, col2 = st.columns(2)

    # Column 1: Upload Files
    with col1:
        st.markdown("### 📂 Upload Your Files")
        st.info(
            "Upload documents containing survey questions or related data. "
            "The system will extract and process relevant survey questions automatically.\n\n"
            "**Supported formats:**\n"
            "- 📄 PDF (.pdf)\n"
            "- 📝 Word Documents (.docx)\n"
            "- 🗂️ JSON Files (.json)\n"
            "- 📑 Text Files (.txt)\n\n"
            "Simply upload your file, and the system will do the rest!"
        )
        if st.button("📤 Upload Files", key="file_button"):
            st.session_state.page = "upload"  # Navigate to upload page
            st.rerun()

    # Column 2: AI-Powered Survey Generator
    with col2:
        st.markdown("### 🤖 AI-Powered Survey Generator")
        st.info(
            "Not sure what survey questions to ask? Let our intelligent chatbot assist you in creating a well-structured survey!\n\n"
            "**How it works:**\n"
            "1. **Step 1:** Provide some basic details about your survey, such as the topic, purpose, and target audience.\n"
            "2. **Step 2:** The chatbot will generate a set of potential survey questions tailored to your needs.\n"
            "3. **Step 3:** You can refine, modify, or regenerate questions until you are satisfied with the results.\n\n"
            "This option is perfect for users who need inspiration and guidance to create an effective survey."
        )
        if st.button("🚀 Use AI", key="ai_button"):
            st.session_state.page = "generate"  # Navigate to generate page
            st.rerun()

    # Footer or additional instructions
    st.markdown("---")  # Divider
    st.markdown(
        "💡 **Tip:** If you have existing survey questions, uploading a file is the fastest option. "
        "Otherwise, use the AI-powered generator for suggestions!"
    )

# Initialize session state for navigation
if "page" not in st.session_state:
    st.session_state.page = "main"

# Navigation logic
if st.session_state.page == "main":
    main_page()
elif st.session_state.page == "upload":
    upload_page()
elif st.session_state.page == "uploading_processing":
    upload_processing_page()
elif st.session_state.page == "ai_processing_staging":
    ai_processing_staging_page()
elif st.session_state.page == "ai_processing_check":
    llm_file_check_page()
elif st.session_state.page == "generate":
    generate_page()
elif st.session_state.page == "confirmation":
    confirmation_page()
elif st.session_state.page == "index_generation":
    index_generation_page()