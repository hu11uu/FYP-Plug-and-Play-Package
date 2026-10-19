import streamlit as st
import os
import random
import string
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from pymongo import MongoClient 
from azure.core.credentials import AzureKeyCredential 
from azure.search.documents import SearchClient
from agentmemory import (
    get_memories
)
from streamlit_lottie import st_lottie
import time
import json

def init():

    placeholder = st.empty()  # Create a placeholder for the loading screen
    progress_text = st.empty()  # Create a placeholder for updating messages

    with placeholder.container():
        col1, col2, col3 = st.columns([0.3, 2.7, 1.5])
        with col1:
            lottie_loading = load_lottilefile("image_assets/loading.json")
            st_lottie(
            lottie_loading,
            speed=1,
            reverse=False,
            loop=True,
            quality="medium",
            height = 110,
            width = 110,
            key=None
        )
            
        with col2:
            st.title("Initializing NTU Teaching Assistant Chatbot...")
        
        st.subheader("Please wait while the system sets up 👍.")
        progress_bar = st.progress(0)

        load_dotenv(override=True)

        #Setting up OpenAI LLM for Azure
        progress_text.write("Setting up Azure OpenAI...")
        st.session_state.openai_llm = AzureChatOpenAI(
            azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'],
            api_key=os.environ['AZURE_OPENAI_APIKEY'],
            deployment_name=os.environ['AZURE_OPENAI_DEPLOYMENT_NAME'],
            model_name=os.environ['AZURE_OPENAI_MODEL_NAME'],
            api_version=os.environ['AZURE_OPENAI_API_VERSION'],
            temperature=0
        )
        progress_bar.progress(20)
        time.sleep(1)

        #Setting up FAISS vector store
        progress_text.write("Setting up FAISS vector store...")
        st.session_state.embeddings = AzureOpenAIEmbeddings(
                    azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'], 
                    api_key=os.environ['AZURE_OPENAI_APIKEY'], 
                    model=os.environ['TEXT_EMBEDDING_MODEL_NAME'],
                    azure_deployment=os.environ['TEXT_EMBEDDING_DEPLOYMENT_NAME'])
        progress_bar.progress(30)
        time.sleep(1)

        #Setting up AI Azure Search
        progress_text.write("Setting up context indexes from Azure AI search...")
        st.session_state.information_search = SearchClient(
                endpoint=os.environ.get('AZURE_AI_SEARCH_ENDPOINT'), 
                index_name=os.environ.get('AZURE_AI_SEARCH_INDEX', 'sc1003_ay24s1'), 
                credential= AzureKeyCredential(os.environ.get('AZURE_AI_SEARCH_API_KEY'))
            )
        
        st.session_state.notes_search = SearchClient(
                endpoint=os.environ.get('AZURE_AI_SEARCH_ENDPOINT'), 
                index_name=os.environ.get('AZURE_AI_SEARCH_INDEX', 'sc1003_ay24s1'), 
                credential= AzureKeyCredential(os.environ.get('AZURE_AI_SEARCH_API_KEY'))
            )

        
        st.session_state.surveyquestions_search = SearchClient(
                endpoint=os.environ.get('AZURE_SURVEY_SEARCH_ENDPOINT') or os.environ.get('AZURE_AI_SEARCH_ENDPOINT'),
                index_name='survey_questions',
                credential=AzureKeyCredential(
                    os.environ.get('AZURE_SURVEY_SEARCH_API_KEY') or os.environ.get('AZURE_AI_SEARCH_API_KEY')
                )
            )
        
        progress_bar.progress(50)
        time.sleep(1)

        # Connect to the chatbot database to update survey data
        progress_text.write("Connecting to chatbot database...")
        st.session_state.sql_client = MongoClient(os.environ['PYMONGO_CONNECTION_STRING'])
        progress_bar.progress(70)
        time.sleep(1)

        progress_text.write("Loading session states and chat history...")
        if 'conversation' not in st.session_state:
            st.session_state.conversation = []
        if 'messages' not in st.session_state:
            st.session_state.messages = []                                        
        if 'survey_shown' not in st.session_state:
            st.session_state.survey_shown = False
        if 'survey_questions' not in st.session_state:
            st.session_state.survey_questions = []
        if 'survey_responses' not in st.session_state:
            st.session_state.survey_responses = []
        if 'conversation_id' not in st.session_state:
            st.session_state.conversation_id = generate_random_code(8)
        if 'new_conversation' not in st.session_state:
            st.session_state.new_conversation = True
        if 'new_conversation_title' not in st.session_state:
            st.session_state.conversation_title = None
        
        # Initialize username if not present
        if 'username' not in st.session_state:
            st.session_state.username = "guest"
        
        if 'cached_human_memories' not in st.session_state:
            st.session_state.cached_human_memories = get_memories(category='human_memories', sort_order="desc", n_results=1000, include_embeddings=False, filter_metadata={'username': st.session_state.username})
        if 'cached_ai_memories' not in st.session_state:
            st.session_state.cached_ai_memories = get_memories(category='ai_memories', sort_order="desc", n_results=1000, include_embeddings=False, filter_metadata={'username': st.session_state.username})
        progress_bar.progress(80)
        if 'chatbot_context' not in st.session_state:
            st.session_state.chatbot_context = None
            chatbot_db = st.session_state.sql_client['chatbot']  # Use the chatbot database
            context_collection = chatbot_db['chatbot_context']
            st.session_state.chatbot_context = (existing_context := context_collection.find_one({}, {"_id": 0, "context": 1})) and existing_context.get("context", "")

        progress_bar.progress(90)

    # Clear the placeholder once setup is complete
    placeholder.empty()
    # Hide the progress bar and text
    progress_text.empty()

#To generate random ID for each new conversation
def generate_random_code(length=8):
    characters = string.ascii_letters + string.digits  # Letters (both uppercase and lowercase) + digits
    random_code = ''.join(random.choice(characters) for _ in range(length))
    return random_code

def load_lottilefile(filepath):
    # Get the directory of the current file and resolve relative paths
    import pathlib
    current_dir = pathlib.Path(__file__).parent.parent
    full_path = current_dir / filepath
    with open(full_path, "r") as f:
        return json.load(f)