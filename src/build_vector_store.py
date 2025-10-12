import os
import csv
import pandas as pd
import fitz  # PyMuPDF
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from .config import PINECONE_INDEX_NAME, DATA_SOURCE_DIR

# --- Data Extraction Functions ---

def extract_from_csv(file_path: str) -> list[dict]:
    """Extracts Q&A data from a CSV file."""
    docs = []
    with open(file_path, mode='r', encoding='utf-8-sig') as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            question = row.get('question', '').split('問：')[-1].strip()
            answer = row.get('answer', '').split('答：')[-1].strip()
            if question and answer:
                docs.append({'question': question, 'answer': answer})
    return docs

def extract_from_xlsx(file_path: str) -> list[dict]:
    """Extracts Q&A data from an Excel file."""
    docs = []
    df = pd.read_excel(file_path)
    for _, row in df.iterrows():
        question = str(row.get('question', ''))
        answer = str(row.get('answer', ''))
        question = question.split('問：')[-1].strip()
        answer = answer.split('答：')[-1].strip()
        if question and answer:
            docs.append({'question': question, 'answer': answer})
    return docs

def extract_from_pdf(file_path: str) -> list[dict]:
    """Extracts Q&A data from a PDF file."""
    docs = []
    text = ""
    with fitz.open(file_path) as doc:
        for page in doc:
            text += page.get_text()
    qa_pairs = text.split('Q:')
    for pair in qa_pairs[1:]:
        parts = pair.split('A:')
        if len(parts) == 2:
            question = parts[0].strip()
            answer = parts[1].strip().split('Q:')[0].strip()
            if question and answer:
                docs.append({'question': question, 'answer': answer})
    return docs


# --- Main Sync Function --- #

def sync_vector_store():
    os.makedirs(DATA_SOURCE_DIR, exist_ok=True)

    print("Initializing services...", flush=True)
    embeddings = OpenAIEmbeddings()
    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("PINECONE_API_KEY must be set.")
    pc = Pinecone(api_key=api_key)

    if PINECONE_INDEX_NAME not in pc.list_indexes().names():
        print(f"Creating index: {PINECONE_INDEX_NAME}", flush=True)
        pc.create_index(name=PINECONE_INDEX_NAME, dimension=1536, metric="cosine", spec=ServerlessSpec(cloud='aws', region='us-east-1'))

    print(f"Loading all documents from '{DATA_SOURCE_DIR}'...", flush=True)
    all_docs_data = []
    for filename in os.listdir(DATA_SOURCE_DIR):
        file_path = os.path.join(DATA_SOURCE_DIR, filename)
        if filename.endswith('.csv'):
            print(f"Processing CSV: {filename}", flush=True)
            all_docs_data.extend(extract_from_csv(file_path))
        elif filename.endswith('.xlsx'):
            print(f"Processing Excel: {filename}", flush=True)
            all_docs_data.extend(extract_from_xlsx(file_path))
        elif filename.endswith('.pdf'):
            print(f"Processing PDF: {filename}", flush=True)
            all_docs_data.extend(extract_from_pdf(file_path))

    if not all_docs_data:
        print("No valid documents found to process.", flush=True)
        return {"status": "error", "message": "No valid documents found to process."}

    local_docs = {}
    for i, doc_data in enumerate(all_docs_data):
        question = doc_data['question']
        answer = doc_data['answer']
        doc_id = f"qa_{abs(hash(question))}_{i}"
        # Create a metadata dict that includes both the answer and the original question text
        metadata = {
            "answer": answer,
            "text": question 
        }
        local_docs[doc_id] = Document(page_content=question, metadata=metadata)
    
    print(f"Loaded {len(local_docs)} Q&A pairs from all sources.", flush=True)

    index = pc.Index(PINECONE_INDEX_NAME)
    print("Fetching existing vector IDs from Pinecone...", flush=True)
    try:
        existing_ids = set()
        for ids_batch in index.list():
            existing_ids.update(ids_batch)
    except Exception as e:
        print(f"Could not fetch existing IDs, assuming index is empty. Error: {e}", flush=True)
        existing_ids = set()
    print(f"Found {len(existing_ids)} existing vectors in Pinecone.", flush=True)

    local_ids = set(local_docs.keys())
    ids_to_upsert = list(local_ids - existing_ids)
    ids_to_delete = list(existing_ids - local_ids)

    if ids_to_upsert:
        print(f"Upserting {len(ids_to_upsert)} new/modified documents...", flush=True)
        for i in range(0, len(ids_to_upsert), 100):
            batch_ids = ids_to_upsert[i:i+100]
            vectors_to_upsert = []
            for doc_id in batch_ids:
                doc = local_docs[doc_id]
                embedding = embeddings.embed_query(doc.page_content)
                vectors_to_upsert.append({"id": doc_id, "values": embedding, "metadata": doc.metadata})
            index.upsert(vectors=vectors_to_upsert)
    else:
        print("No new or modified documents to upsert.", flush=True)

    if ids_to_delete:
        print(f"Deleting {len(ids_to_delete)} outdated documents...", flush=True)
        index.delete(ids=ids_to_delete)
    else:
        print("No documents to delete.", flush=True)

    final_count = len(local_ids)
    print(f"Sync complete. Total vectors: {final_count}. Upserted: {len(ids_to_upsert)}, Deleted: {len(ids_to_delete)}.", flush=True)
    return {"status": "success", "message": f"Sync complete. Upserted: {len(ids_to_upsert)}, Deleted: {len(ids_to_delete)}. Total vectors: {final_count}."}


if __name__ == '__main__':
    try:
        result = sync_vector_store()
        print(result)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
