
import os
from langchain_community.document_loaders import CSVLoader
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, PodSpec

# --- Configuration --- #
# Ensure these environment variables are set before running this script
# os.environ["OPENAI_API_KEY"] = "YOUR_OPENAI_API_KEY"
# os.environ["PINECONE_API_KEY"] = "YOUR_PINECONE_API_KEY"
# os.environ["PINECONE_ENVIRONMENT"] = "YOUR_PINECONE_ENVIRONMENT" # e.g., "gcp-starter"

PINECONE_INDEX_NAME = "carbon-assistant-qa-index" # Changed index name for Q&A
USER_MANUAL_CSV = "generated_user_manual.xlsx.csv"

def build_vector_store():
    # 1. Initialize OpenAI Embeddings
    print("Initializing OpenAI Embeddings...")
    embeddings = OpenAIEmbeddings()

    # 2. Initialize Pinecone
    print("Initializing Pinecone client...")
    api_key = os.environ.get("PINECONE_API_KEY")
    environment = os.environ.get("PINECONE_ENVIRONMENT")
    if not api_key or not environment:
        raise ValueError("PINECONE_API_KEY and PINECONE_ENVIRONMENT environment variables must be set.")
    
    pc = Pinecone(api_key=api_key, environment=environment)

    # Check if index already exists, if not, create it
    if PINECONE_INDEX_NAME not in pc.list_indexes():
        print(f"Creating Pinecone index: {PINECONE_INDEX_NAME}...")
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=1536,  # OpenAI embeddings dimension
            metric="cosine",
            spec=PodSpec(environment=environment)
        )
        print("Index created.")
    else:
        print(f"Pinecone index {PINECONE_INDEX_NAME} already exists.")

    # 3. Load documents from CSV
    print(f"Loading Q&A from {USER_MANUAL_CSV}...")
    if not os.path.exists(USER_MANUAL_CSV):
        print(f"Error: '{USER_MANUAL_CSV}' not found. Please ensure it's in the project root.")
        return

    # Use CSVLoader, specifying 'question' as the source column for embeddings
    # The entire row (question and answer) will be in page_content by default
    loader = CSVLoader(file_path=USER_MANUAL_CSV, csv_args={'delimiter': ','})
    documents = loader.load()

    if not documents:
        print(f"No Q&A data found in '{USER_MANUAL_CSV}'. Please check the file content.")
        return

    print(f"Loaded {len(documents)} Q&A pairs.")

    # 4. Split documents into chunks (each Q&A pair is already a chunk, but splitter can ensure consistent size if needed)
    # For Q&A pairs, we might not need aggressive splitting, but a splitter can still be useful for metadata handling or very long answers.
    # Let's use a simple splitter to ensure each document is treated as a chunk.
    print("Splitting documents into chunks (each Q&A pair is a chunk)...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=0) # No overlap needed for distinct Q&A
    docs = text_splitter.split_documents(documents)
    print(f"Split into {len(docs)} chunks.")

    # 5. Upsert into Pinecone
    print(f"Upserting {len(docs)} chunks into Pinecone index {PINECONE_INDEX_NAME}...")
    # The from_documents method handles embedding and upserting
    vectorstore = PineconeVectorStore.from_documents(docs, embeddings, index_name=PINECONE_INDEX_NAME)
    print("Vector store built and documents upserted to Pinecone.")

if __name__ == '__main__':
    try:
        build_vector_store()
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
