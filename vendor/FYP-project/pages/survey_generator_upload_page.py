import streamlit as st
from helper.upload_page_processing import *

def upload_page():
    if st.button("⬅️ Back to Main Page"):
        keys_to_keep = {"username", "authenticated"}
        st.session_state.page = "main" 
        for key in list(st.session_state.keys()): 
            if key not in keys_to_keep:
                del st.session_state[key]
        st.rerun()

    st.title("📤 Upload Your Survey Files")
        
    st.markdown("### Upload your survey questions or related data here")

    # Store uploaded file in session state
    if "uploaded_file" not in st.session_state:
        st.session_state.uploaded_file = None

    # File uploader
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "docx", "json", "txt"],
        help="Supported formats: PDF, Word, JSON, Text",
        accept_multiple_files=False,
    )
    
    if uploaded_file is not None:
        st.session_state.uploaded_file = uploaded_file
        st.success(f"File '{uploaded_file.name}' uploaded successfully!")
            
        # Add processing button
        if st.button("Process File"):
            with st.spinner("Processing your file..."):
                st.session_state.page = "uploading_processing"
                st.rerun()

    else:
        # Create tabs for different file formats
        json_tab, txt_tab, pdf_tab, docx_tab = st.tabs(["JSON Format", "TXT Format", "PDF Format", "DOCX Format"])
        
    
        with json_tab:
            st.info("""
            📑 **JSON File Structure Guidelines**
            
            Your JSON file should follow this structure:
            ```json
            {
                "questions": [
                    "Question 1",
                    "Question 2",
                    ...
                ]
            }
            ```
            
            **Requirements:**
            • Each question must be a Likert scale question (Able to be answered with a 1-5 scale)
            • Questions should be clear and concise
            
            **Example:**
            ```json
            {
                "questions": [
                    "What is your level of satisfaction with our service?",
                    "How likely are you to recommend our product to a friend?",
                    "What improvements would you like to see?",
                    "How often do you use our platform?"
                ]
            }
            ```
            """)

        with txt_tab:
            st.info("""
            📄 **TXT File Structure Guidelines**
            
            Your text file should follow this format:
            ```
            Q: [Your Likert scale question]

            Q: [Next question]
                    
            ...
            ```
            
            **Preferable Requirements:**
            • Start each question with "Q: "
            • Leave a blank line between questions
            
            **Example:**
            ```
            Q: I feel adequately recognized and rewarded for my contributions.

            Q: The company provides sufficient opportunities for professional development.
                    
            ...
            ```
            """)

        with pdf_tab:
            st.info("""
            📚 **PDF File Structure Guidelines**
            
            Your PDF should be formatted as follows:
            
            **Document Structure:**
            • Each question should be on a new line
            • If there are multiple sections, use clear headings for each section
            
            **Format Example:**
            ```
            Section 1 (Section Title):
            ---------------
            
            Question 1:
            I feel adequately recognized and rewarded for my contributions.
            
            Question 2:
            The company provides sufficient opportunities for professional development.
            
            Section 2 (Section Title):
            ---------------
            
            ...
            ```
            
            **Note:** PDF files will be processed using OCR technology. Please ensure:
            • Clear, readable font
            • Proper formatting and spacing
            • No complex layouts or tables
            • Minimal styling and graphics
            """)

        with docx_tab:
            st.info("""
            📝 **DOCX File Structure Guidelines**
            
            Your Word document should follow this structure:
            
            **Document Format:**
            • Each question should be on a new line  
            • If there are multiple sections, use clear headings for each section
            
            **Format Example:**
            ```
            Section 1 (Section Title):
            ---------------
            
            Question 1
            I feel adequately recognized and rewarded for my contributions.
            
            Question 2
            The company provides sufficient opportunities for professional development.
                    
            Section 2 (Section Title):
            ---------------
                    
            ...
            ```
            
            **Formatting Tips:**
            • Use standard fonts (Arial, Times New Roman)
            • Maintain consistent spacing
            • Avoid complex formatting
            • Don't use tables or text boxes
            """)

        # Help section
        with st.expander("❓ Need Help?"):
            st.markdown("""
            **Common Issues:**
            1. **File Format**: Ensure your file matches the template structure
            2. **Question Format**: Questions should be clear and in Likert scale format
            
            **Best Practices:**
            • Keep questions concise and focused
            • Use relevant and specific tags
            • Avoid duplicate questions
            """)
