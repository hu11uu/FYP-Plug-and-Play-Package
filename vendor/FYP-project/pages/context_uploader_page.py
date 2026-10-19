import streamlit as st
from pymongo import MongoClient 
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from langchain_text_splitters import CharacterTextSplitter
from langchain_openai import AzureOpenAIEmbeddings
import uuid
import tempfile
from pathlib import Path
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader
from azure.search.documents.indexes.models import SearchIndex, SearchField, SearchFieldDataType, SimpleField, SearchableField, VectorSearch, VectorSearchProfile, HnswAlgorithmConfiguration, AzureOpenAIVectorizer,AzureOpenAIVectorizerParameters
from azure.search.documents.indexes import SearchIndexClient

# Set page configuration
st.set_page_config(layout="wide")

if "sql_client" not in st.session_state:
    st.session_state.sql_client = MongoClient(os.environ['PYMONGO_CONNECTION_STRING'])

chatbot_db = st.session_state.sql_client['chatbot']  # Use the chatbot database
context_collection = chatbot_db['chatbot_context']

def context_uploader_page():

    st.title("Chatbot customisation")
    # Create tabs for different file formats
    context_tab, information_tab, notes_tab = st.tabs(["Upload Context Tab", "Upload Information Tab", "Upload Notes Tab"])

    with context_tab:
        st.markdown("### **🔒 Enter Your Chatbot Context here:**")

        st.info("""
        ### **Why define a context?**
                        
        Your chatbot will only respond to topics within the context you define.\n
        This ensures users cannot ask unrelated questions.

        📌 **How to Specify the Context?**
        Enter a short phrase describing the chatbot’s domain and scope:

        **Format:**  
        📍 `[Industry/Field] [Main Focus] [Relevant Topics]`

        ✅ **Examples**:
        - **Healthcare**: "hospital patient admission and medical records management"
        - **Retail**: "e-commerce fashion inventory and customer support"
        - **Education**: "university course registration and student academic records"
        """)

        if "context_success" not in st.session_state:
            st.session_state.context_success = False  # Initialize session state variable

        if "chatbot_context" not in st.session_state:
            existing_context = context_collection.find_one({}, {"_id": 0, "context": 1}) or {}
            st.session_state.chatbot_context = existing_context.get("context", "")
        
        st.write("**This is the current context:**", st.session_state.chatbot_context)
        context_phrase = st.text_input("Context", label_visibility="collapsed")

        if st.button("Save Context"):
            new_context = context_phrase.strip()
            if new_context and new_context != st.session_state.chatbot_context:
                if st.session_state.chatbot_context:
                    # Update existing context
                    context_collection.update_one({}, {"$set": {"context": new_context}})
                else:
                    # Insert a new context if it doesn't exist
                    context_collection.insert_one({"context": new_context})

                # Update session state
                st.session_state.chatbot_context = new_context
                st.session_state.context_success = True
                st.rerun()  # Force rerun for immediate UI update

        # Show success message after rerun
        if st.session_state.context_success:
            st.success("✅ Context updated successfully!")


    with information_tab:
        st.markdown("### **🔒 Upload information context for the chatbot here:**")

        st.info("""
        ### **Why upload documents?**
        
        The chatbot will use this information to provide personalized responses based on your uploaded content. \n
        Without these documents, the chatbot won't have access to your specific information.
                
        ### Upload documents containing:
        
        • Personal or business FAQs \n
        • Company policies or procedures \n
        • Product information \n 
        • Specialized knowledge not available to the general AI \n
        """)

         # Store uploaded file in session state
        if "uploaded_information_file" not in st.session_state:
            st.session_state.uploaded_information_file = None

        uploaded_information_files = st.file_uploader(
            "Choose files (multiple files supported):",
            type=["pdf", "docx"],
            help="Supported formats: PDF, Word",
            accept_multiple_files=True,  # Allow multiple files
            key="information_uploader"
        )

        if uploaded_information_files:
            st.session_state.uploaded_notes_files = uploaded_information_files

            if st.button("Process Information Files"):
                status_text = st.empty()
                progress_bar = st.progress(0)
                temp_dir = tempfile.TemporaryDirectory()

                try:
                    total_files = len(uploaded_information_files)
                    all_success = True

                    # Process each uploaded file
                    for file_idx, file in enumerate(uploaded_information_files):
                        # Update progress text and bar
                        progress_value = (file_idx+1) / total_files
                        status_text.text(f"Processing {file.name} ({file_idx+1}/{total_files})")
                        progress_bar.progress(progress_value)
                        
                        # Load single file's documents
                        documents = []
                        temp_path = os.path.join(temp_dir.name, file.name)
                        with open(temp_path, "wb") as f:
                            f.write(file.getbuffer())
                            
                        # Load document based on file type
                        if file.name.endswith(".pdf"):
                            loader = PyPDFLoader(temp_path)
                        elif file.name.endswith(".docx"):
                            loader = Docx2txtLoader(temp_path)
                        else:
                            continue
                            
                        loaded_docs = loader.load()
                        
                        # Add source metadata
                        for doc in loaded_docs:
                            doc.metadata['source'] = file.name
                        documents.extend(loaded_docs)

                        # Call your document processing function
                        result = add_or_update_docs(
                            documents=documents,
                            index_name="information",
                            
                        )

                        if result is not True:
                            st.error(f"Error processing {file.name}: {result}")
                            all_success = False
                            break  # Exit on first error
                    
                    if all_success:
                        status_text.text("Processing complete!")
                        st.success("Files processed successfully!")
                        st.balloons()

                except Exception as e:
                    st.error(f"Unexpected error: {str(e)}")
                finally:
                    temp_dir.cleanup()
                    
    with notes_tab:
        st.markdown("### **🔒 Upload extra notes for your chatbot here:**")

        st.info("""
        ### Why upload notes?

        Adding these documents helps the chatbot provide more relevant and informed responses based on your specific materials. \n
        The chatbot will reference these documents when answering your questions, making conversations more productive and tailored to your needs.

        ### Upload documents containing:
        - Study notes and learning materials \n
        - Research papers and articles \n
        - Books and published content \n 
        - Meeting notes or presentations \n 
        - Project documentation \n 
        - Any text you want the chatbot to reference \n
        """)

        uploaded_notes_files = st.file_uploader(
            "Choose files (multiple files supported):",
            type=["pdf", "docx"],
            help="Supported formats: PDF, Word",
            accept_multiple_files=True,  # Allow multiple files
            key="notes_uploader"
        )

        if uploaded_notes_files:
            st.session_state.uploaded_notes_files = uploaded_notes_files

            if st.button("Process Note Files"):
                status_text = st.empty()
                progress_bar = st.progress(0)
                temp_dir = tempfile.TemporaryDirectory()

                try:
                    total_files = len(uploaded_notes_files)
                    all_success = True

                    # Process each uploaded file
                    for file_idx, file in enumerate(uploaded_notes_files):
                        # Update progress text and bar
                        progress_value = (file_idx+1) / total_files
                        status_text.text(f"Processing {file.name} ({file_idx+1}/{total_files})")
                        progress_bar.progress(progress_value)
                        
                        # Load single file's documents
                        documents = []
                        temp_path = os.path.join(temp_dir.name, file.name)
                        with open(temp_path, "wb") as f:
                            f.write(file.getbuffer())
                            
                        # Load document based on file type
                        if file.name.endswith(".pdf"):
                            loader = PyPDFLoader(temp_path)
                        elif file.name.endswith(".docx"):
                            loader = Docx2txtLoader(temp_path)
                        else:
                            continue
                            
                        loaded_docs = loader.load()
                        
                        # Add source metadata
                        for doc in loaded_docs:
                            doc.metadata['source'] = file.name
                        documents.extend(loaded_docs)

                        # Call your document processing function
                        result = add_or_update_docs(
                            documents=documents,
                            index_name="notes"  
                        )

                        if result is not True:
                            st.error(f"Error processing {file.name}: {result}")
                            all_success = False
                            break  # Exit on first error
                    
                    if all_success:
                        status_text.text("Processing complete!")
                        st.success("Files processed successfully!")
                        st.balloons()

                except Exception as e:
                    st.error(f"Unexpected error: {str(e)}")
                finally:
                    temp_dir.cleanup()

def add_or_update_docs(documents, index_name):
    try:
        # Validate input documents
        if not documents:
            return True

        # Get filename from first document (assume all docs are from same source)
        first_doc = documents[0]
        filename = Path(first_doc.metadata['source']).name

        # Validate all documents share the same source
        for doc in documents:
            current_filename = Path(doc.metadata['source']).name
            if current_filename != filename:
                raise ValueError("All documents must be from the same source file")

        # Initialize embeddings and search client
        embeddings = AzureOpenAIEmbeddings(
            azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'],
            api_key=os.environ['AZURE_OPENAI_APIKEY'],
            model=os.environ['TEXT_EMBEDDING_MODEL_NAME'],
            azure_deployment=os.environ['TEXT_EMBEDDING_DEPLOYMENT_NAME']
        )

        search_client = SearchClient(
            endpoint=os.environ['AZURE_AI_SEARCH_ENDPOINT'],
            index_name=index_name,
            credential=AzureKeyCredential(os.environ['AZURE_AI_SEARCH_API_KEY'])
        )

        # Combine all pages into a single text
        combined_text = "\n".join([doc.page_content for doc in documents])
        
        # Split by character chunks
        text_splitter = CharacterTextSplitter(
            chunk_size=1000, 
            chunk_overlap=200, 
            separator="\n"
        )
        chunks = text_splitter.split_text(combined_text)

        # Generate embeddings for all chunks at once (more efficient).
        # Fix #7 — moved above the index check/recreation below so the
        # actual embedding dimension is known before create_index() is
        # ever called, instead of hardcoding 1536 regardless of the real
        # model's output size.
        chunk_embeddings = embeddings.embed_documents(chunks)

        # Fix #7 — this used to hard-code checks/deletes against the
        # "information" index regardless of which index_name was actually
        # passed in, meaning uploading files from the *notes* tab could
        # silently test/delete/recreate the *information* index instead —
        # every customisation upload risked wiping the wrong index. Now
        # uses the actual index_name parameter throughout, and the real
        # embedding dimension from the embeddings just generated instead
        # of a hardcoded 1536 that may not match the deployed model.
        index_exists = get_index(index_name)
        print(index_exists)

        if index_exists:
            delete_index(index_name)
            create_index(index_name, len(chunk_embeddings[0]))

        # Check for existing documents with this filename
        search_results = list(search_client.search(filter=f"filename eq '{filename}'"))
        existing_ids = [result['id'] for result in search_results]

        # Delete existing documents if any
        if existing_ids:
            search_client.delete_documents([{"id": id} for id in existing_ids])

        # Prepare documents for upload
        docs_to_upload = [
            {
                "id": str(uuid.uuid4()),
                "content": chunk,
                "content_vector": chunk_embeddings[i],
                "filename": filename
            }
            for i, chunk in enumerate(chunks)
        ]

        # Upload all new chunks
        if docs_to_upload:
            search_client.upload_documents(docs_to_upload)

        return True

    except Exception as e:
        print(f"An error occurred: {e}")
        return e
    
def create_index(index_name, embedding_dimension):
    try:
        # Defines instance of SearchIndexClient class
        search_index_client = SearchIndexClient(
            os.environ.get('AZURE_AI_SEARCH_ENDPOINT'), 
            AzureKeyCredential(os.environ.get('AZURE_AI_SEARCH_API_KEY'))
        )
        
        # Defines the structure of the search index
        fields = [
            # Basic indexing
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=True,
                retrievable=True,
                stored=True,
                sortable=False,
                facetable=False
            ),
            # Main content field
            SearchableField(
                name="content",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=False,
                retrievable=True,
                stored=True,
                sortable=False,
                facetable=False
            ),
            # Filename
            SearchableField(
            name="filename",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
            ),
            # Content embeddings vector
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=embedding_dimension,  # Use the same embedding dimension
                vector_search_profile_name="myHnswProfile"
            )
        ]

        # Configure the vector search configuration  
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="myHnsw"
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name="myHnswProfile",
                    algorithm_configuration_name="myHnsw",
                    vectorizer_name="myVectorizer"
                )
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="myVectorizer",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=os.environ.get('AZURE_OPENAI_ENDPOINT'),
                        api_key=os.environ.get('AZURE_OPENAI_APIKEY'),
                        model_name=os.environ.get('TEXT_EMBEDDING_MODEL_NAME'),
                        deployment_name=os.environ.get('TEXT_EMBEDDING_DEPLOYMENT_NAME')
                    )
                )
            ]
        )

        # Define the search index
        searchindex = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
        search_index_client.create_or_update_index(index=searchindex)
        return index_name
    except Exception as e:
        print(f"Error creating index: {e}")
        return None
    
def delete_index(index_name):
    search_index_client = SearchIndexClient(os.environ.get('AZURE_AI_SEARCH_ENDPOINT'), 
                                   AzureKeyCredential(os.environ.get('AZURE_AI_SEARCH_API_KEY')))
    try:
       search_index_client.delete_index(index_name)
       return True
    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    
def get_index(index_name):

    index_exists = True
    search_client = SearchIndexClient(
            endpoint=os.environ['AZURE_AI_SEARCH_ENDPOINT'],
            credential=AzureKeyCredential(os.environ['AZURE_AI_SEARCH_API_KEY'])
        )
    try:
       search_client.get_index(index_name)
    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    
    return index_exists

context_uploader_page()