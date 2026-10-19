from langchain_openai import AzureOpenAIEmbeddings
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SearchField, SearchFieldDataType, SimpleField, SearchableField, VectorSearch, VectorSearchProfile, HnswAlgorithmConfiguration, AzureOpenAIVectorizer,AzureOpenAIVectorizerParameters
import uuid
import os
import streamlit as st


def _survey_search_credentials():
    """
    survey_questions now lives on its own dedicated Azure AI Search resource
    (AZURE_SURVEY_SEARCH_ENDPOINT / AZURE_SURVEY_SEARCH_API_KEY) instead of
    the shared service used for information/notes — the shared service is
    capped at 3 indexes and was already full. Falls back to the original
    shared AZURE_AI_SEARCH_* vars if the dedicated ones aren't set, so this
    doesn't break for anyone still using a single service.
    """
    endpoint = os.environ.get('AZURE_SURVEY_SEARCH_ENDPOINT') or os.environ.get('AZURE_AI_SEARCH_ENDPOINT')
    api_key = os.environ.get('AZURE_SURVEY_SEARCH_API_KEY') or os.environ.get('AZURE_AI_SEARCH_API_KEY')
    return endpoint, api_key


def load_embeddings():

    # Defines the instance of AzureOpenAIEmbedding class
    st.session_state.embeddings = AzureOpenAIEmbeddings(azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'], 
                                    api_key=os.environ['AZURE_OPENAI_APIKEY'], 
                                    model=os.environ['TEXT_EMBEDDING_MODEL_NAME'],
                                    azure_deployment=os.environ['TEXT_EMBEDDING_DEPLOYMENT_NAME'])

    # Finds the dimension of the embedding model
    sample_text = "Embeddings dimension finder"
    embedding_vector = st.session_state.embeddings.embed_query(sample_text)
    embedding_dimension = len(embedding_vector)

    return embedding_dimension

def create_index(index_name, embedding_dimension):
    try:
        # Defines instance of SearchIndexClient class
        _endpoint, _key = _survey_search_credentials()
        search_index_client = SearchIndexClient(
            _endpoint,
            AzureKeyCredential(_key)
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
            # Tags field
            SearchableField(
                name="tags",
                type=SearchFieldDataType.String,
                searchable=True,  # Enables searching on tag names
                filterable=True,
                retrievable=True,
                stored=True,
                sortable=False,
                facetable=False
            ),
            # Tag embeddings vector
            SearchField(
                name="tags_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=embedding_dimension,  # Use the same embedding dimension
                vector_search_profile_name="myHnswProfile"
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
    
def delete_index_function(index_name):
    _endpoint, _key = _survey_search_credentials()
    search_index_client = SearchIndexClient(_endpoint, AzureKeyCredential(_key))
    try:
       search_index_client.delete_index(index_name)
       return True
    except Exception as e:
        print(f"An error occurred: {e}")
        return False

def add_or_update_docs(documents, index_name):

    try:
        # Initialize the SearchClient
        _endpoint, _key = _survey_search_credentials()
        search_client = SearchClient(
            endpoint=_endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(_key)
        )

        # Defines the instance of AzureOpenAIEmbedding class
        embeddings = AzureOpenAIEmbeddings(azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'], 
                                    api_key=os.environ['AZURE_OPENAI_APIKEY'], 
                                    model=os.environ['TEXT_EMBEDDING_MODEL_NAME'],
                                    azure_deployment=os.environ['TEXT_EMBEDDING_DEPLOYMENT_NAME'])
        
        docs_to_add_final = []
        docs_to_update_final = []

        for doc in documents["questions"]:
            question = doc.get('question', '').strip()
            tags = doc.get('tags', [])  # Ensure we get a list

            if not question:
                continue  # Skip empty sentences

            # Generate embedding for content
            try:
                sentence_embedding = embeddings.embed_query(question)
            except Exception as e:
                print(f"Failed to generate embedding for content: {question}")
                print(f"Error: {e}")
                continue
            
            tags_embedding = None
            if tags:
                try:
                    tags_embedding = embeddings.embed_query(' '.join(tags))
                except Exception as e:
                    print(f"Failed to generate tag embeddings: {e}")
                    continue

            # Prepare the document for the search index
            search_doc = {
                'id': str(uuid.uuid4()),
                'content': question,
                'content_vector': sentence_embedding,
                'tags': ', '.join(tags),  # Flatten tags into a comma-separated string
            }
            
            # Add the tags_vector only if the embedding is generated
            if tags_embedding is not None:
                search_doc['tags_vector'] = tags_embedding

            # Check if the content already exists in the index
            search_results = list(search_client.search(search_doc['content']))
            
            if search_results:
                docs_to_update_final.append(search_doc)
            else:
                docs_to_add_final.append(search_doc)

        # Upload or merge documents to the index
        if docs_to_update_final:
            search_client.merge_documents(docs_to_update_final)
        if docs_to_add_final:
            search_client.upload_documents(docs_to_add_final)

        print(f"Processed {len(documents)} sentences")
        print(f"Added {len(docs_to_add_final)} new sentences")
        print(f"Updated {len(docs_to_update_final)} existing sentences")
        return True

    except Exception as e:
        print(f"An error occurred: {e}")
        return False