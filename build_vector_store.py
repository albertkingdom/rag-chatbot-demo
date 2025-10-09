import os
import csv
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec


PINECONE_INDEX_NAME = "carbon-assistant-qa-index"
USER_MANUAL_CSV = "generated_user_manual.xlsx.csv"


def build_vector_store():
    # 1. Initialize OpenAI Embeddings
    print("Initializing OpenAI Embeddings...")
    embeddings = OpenAIEmbeddings()

    # 2. Initialize Pinecone
    print("Initializing Pinecone client...")
    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("PINECONE_API_KEY environment variable must be set.")

    pc = Pinecone(api_key=api_key)

    # 3. Check if index already exists, if not, create it
    if PINECONE_INDEX_NAME not in pc.list_indexes().names():
        print(f"Creating Pinecone index: {PINECONE_INDEX_NAME}...")
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=1536,  # OpenAI embeddings dimension
            metric="cosine",
            spec=ServerlessSpec(cloud='aws', region='us-east-1')
        )
        print("Index created.")
    else:
        print(f"Pinecone index '{PINECONE_INDEX_NAME}' already exists.")

    # 4. Load documents from CSV manually for precise control
    print(f"Loading Q&A from {USER_MANUAL_CSV}...")
    if not os.path.exists(USER_MANUAL_CSV):
        print(f"Error: '{USER_MANUAL_CSV}' not found. Please ensure it's in the project root.")
        return

    documents = []
    with open(USER_MANUAL_CSV, mode='r', encoding='utf-8-sig') as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            question = row.get('question', '').split('問：')[-1].strip()
            answer = row.get('answer', '').split('答：')[-1].strip()
            if question and answer:
                documents.append(Document(page_content=question, metadata={"answer": answer}))

    if not documents:
        print(f"No valid Q&A data found in '{USER_MANUAL_CSV}'.")
        return

    print(f"Loaded and processed {len(documents)} Q&A pairs.")

    # 5. Upsert into Pinecone
    print(f"Upserting {len(documents)} documents into Pinecone index '{PINECONE_INDEX_NAME}'...")
    PineconeVectorStore.from_documents(documents, embeddings, index_name=PINECONE_INDEX_NAME)
    print("Vector store built and documents upserted to Pinecone.")


if __name__ == '__main__':
    try:
        build_vector_store()
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")