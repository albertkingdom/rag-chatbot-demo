import os
import csv
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec


PINECONE_INDEX_NAME = "carbon-assistant-qa-index"
USER_MANUAL_CSV = "generated_user_manual.xlsx.csv"


def sync_vector_store():
    # 1. Initialize OpenAI Embeddings
    print("Initializing OpenAI Embeddings...")
    embeddings = OpenAIEmbeddings()

    # 2. Initialize Pinecone
    print("Initializing Pinecone client...")
    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("PINECONE_API_KEY environment variable must be set.")
    pc = Pinecone(api_key=api_key)

    # 3. Check if index exists, if not, create it
    if PINECONE_INDEX_NAME not in pc.list_indexes().names():
        print(f"Creating Pinecone index: {PINECONE_INDEX_NAME}...")
        pc.create_index(
            name=PINECONE_INDEX_NAME,
            dimension=1536,
            metric="cosine",
            spec=ServerlessSpec(cloud='aws', region='us-east-1')
        )
        print("Index created.")
    else:
        print(f"Pinecone index '{PINECONE_INDEX_NAME}' already exists.")

    # 4. Load local documents and create a dictionary with deterministic IDs
    print(f"Loading local Q&A from {USER_MANUAL_CSV}...")
    if not os.path.exists(USER_MANUAL_CSV):
        message = f"Error: '{USER_MANUAL_CSV}' not found. Please ensure it's in the project root."
        print(message)
        return {"status": "error", "message": message}

    local_docs = {}
    with open(USER_MANUAL_CSV, mode='r', encoding='utf-8-sig') as infile:
        reader = csv.DictReader(infile)
        for i, row in enumerate(reader):
            question = row.get('question', '').split('問：')[-1].strip()
            answer = row.get('answer', '').split('答：')[-1].strip()
            if question and answer:
                doc_id = f"qa_{abs(hash(question))}_{i}"
                local_docs[doc_id] = Document(page_content=question, metadata={"answer": answer})

    if not local_docs:
        message = f"No valid Q&A data found in '{USER_MANUAL_CSV}'."
        print(message)
        return {"status": "error", "message": message}

    print(f"Loaded {len(local_docs)} Q&A pairs from local file.")

    # 5. Get existing IDs from Pinecone
    index = pc.Index(PINECONE_INDEX_NAME)
    print("Fetching existing vector IDs from Pinecone...")
    try:
        # The official list() method returns an iterator of ID batches.
        # We need to iterate through it to build the full set of IDs.
        existing_ids = set()
        for ids_batch in index.list():
            existing_ids.update(ids_batch)
    except Exception as e:
        print(f"Could not fetch existing IDs, assuming index is empty. Error: {e}")
        existing_ids = set()
    print(f"Found {len(existing_ids)} existing vectors in Pinecone.")

    # 6. Determine which docs to upsert and which to delete
    local_ids = set(local_docs.keys())
    ids_to_upsert = list(local_ids)
    ids_to_delete = list(existing_ids - local_ids)

    # 7. Perform upsert and delete operations
    if ids_to_upsert:
        print(f"Upserting {len(ids_to_upsert)} documents...")
        for i in range(0, len(ids_to_upsert), 100):
            batch_ids = ids_to_upsert[i:i+100]
            batch_docs = [local_docs[doc_id] for doc_id in batch_ids]
            # Note: from_documents is not ideal for upserting with specific IDs in this flow.
            # A more direct approach using index.upsert is better.
            vectors_to_upsert = []
            for doc_id, doc in zip(batch_ids, batch_docs):
                embedding = embeddings.embed_query(doc.page_content)
                vectors_to_upsert.append({"id": doc_id, "values": embedding, "metadata": doc.metadata})
            index.upsert(vectors=vectors_to_upsert)
    else:
        print("No new or modified documents to upsert.")

    if ids_to_delete:
        print(f"Deleting {len(ids_to_delete)} outdated documents...")
        index.delete(ids=ids_to_delete)
    else:
        print("No documents to delete.")

    final_count = len(local_docs)
    print(f"Synchronization complete. Index now contains {final_count} vectors.")
    return {"status": "success", "message": f"Synchronization complete. Upserted: {len(ids_to_upsert)}, Deleted: {len(ids_to_delete)}. Total vectors: {final_count}."}


if __name__ == '__main__':
    try:
        result = sync_vector_store()
        print(result)
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
