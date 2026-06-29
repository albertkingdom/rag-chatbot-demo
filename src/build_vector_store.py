import os
import csv
import uuid as uuid_lib
import pandas as pd
import fitz  # PyMuPDF
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from .config import PINECONE_INDEX_NAME, DATA_SOURCE_DIR
from langsmith import traceable
from .bm25_index import BM25Index

# --- Data Extraction Functions ---

def extract_from_csv(file_path: str) -> list[dict]:
    """Extracts Q&A data from a CSV file."""
    docs = []
    with open(file_path, mode='r', encoding='utf-8-sig') as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            question = row.get('question', '').split('問：')[-1].strip()
            answer = row.get('answer', '').split('答：')[-1].strip()
            uuid_val = (row.get('uuid') or '').strip() or None
            if question and answer:
                docs.append({'question': question, 'answer': answer, 'uuid': uuid_val})
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
        raw_uuid = row.get('uuid', None)
        uuid_val = None if pd.isna(raw_uuid) else (str(raw_uuid).strip() or None)
        if question and answer:
            docs.append({'question': question, 'answer': answer, 'uuid': uuid_val})
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
                # PDF sources carry no uuid column; identity is generated at sync time.
                docs.append({'question': question, 'answer': answer, 'uuid': None})
    return docs


# --- UUID backfill --- #

def ensure_uuids_in_source(file_path: str) -> int:
    """Ensure every data row in a csv/xlsx source carries a stable `uuid`.

    Rows lacking a `uuid` are assigned a fresh `uuid4()` and the file is
    rewritten in place using a temp-file + atomic replace so a partial
    write cannot corrupt the source. Returns the number of uuids added.

    Only `.csv` and `.xlsx` are written back; other formats (e.g. PDF) carry
    no uuid column and are handled by in-memory generation at sync time.
    """
    if file_path.endswith('.csv'):
        return _ensure_uuids_csv(file_path)
    if file_path.endswith('.xlsx'):
        return _ensure_uuids_xlsx(file_path)
    return 0


def _ensure_uuids_csv(file_path: str) -> int:
    with open(file_path, mode='r', encoding='utf-8-sig', newline='') as infile:
        reader = csv.DictReader(infile)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    added = 0
    if 'uuid' not in fieldnames:
        fieldnames.append('uuid')
    for row in rows:
        if not (row.get('uuid') or '').strip():
            row['uuid'] = str(uuid_lib.uuid4())
            added += 1

    if added == 0:
        return 0

    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, mode='w', encoding='utf-8-sig', newline='') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, file_path)
    return added


def _ensure_uuids_xlsx(file_path: str) -> int:
    df = pd.read_excel(file_path)
    if 'uuid' not in df.columns:
        df['uuid'] = None

    added = 0
    for idx in df.index:
        raw = df.at[idx, 'uuid']
        if pd.isna(raw) or not str(raw).strip():
            df.at[idx, 'uuid'] = str(uuid_lib.uuid4())
            added += 1

    if added == 0:
        return 0

    tmp_path = f"{file_path}.tmp.xlsx"
    df.to_excel(tmp_path, index=False, engine="openpyxl")
    os.replace(tmp_path, file_path)
    return added


# --- Main Sync Function --- #

@traceable(name="Knowledge Base Sync")
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
        if filename.endswith(('.csv', '.xlsx', '.pdf')):
            # Backfill stable uuids into the source file before extraction so
            # the assigned identity is durable across future syncs. A write-back
            # failure aborts the sync rather than upserting with unstable ids.
            try:
                added = ensure_uuids_in_source(file_path)
                if added:
                    print(f"Backfilled {added} uuid(s) into {filename}", flush=True)
            except Exception as wb_err:
                print(f"Error: failed to write uuids back to {filename} ({wb_err}). Aborting sync.", flush=True)
                return {"status": "error", "message": f"Failed to persist uuids to source file '{filename}': {wb_err}"}

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

    # Deduplicate exact (question, answer) pairs; key surviving docs by their
    # stable uuid (= doc_id). Rows that share a question but differ in answer
    # have distinct uuids and are both retained.
    local_docs = {}
    seen_qa_pairs = set()
    duplicates_skipped = 0

    for doc_data in all_docs_data:
        question = doc_data['question']
        answer = doc_data['answer']

        qa_pair_key = (question, answer)
        if qa_pair_key in seen_qa_pairs:
            duplicates_skipped += 1
            continue
        seen_qa_pairs.add(qa_pair_key)

        # Stable doc_id sourced from the uuid column; PDF/other rows without a
        # persisted uuid get one generated in memory here.
        doc_id = doc_data.get('uuid') or str(uuid_lib.uuid4())
        if doc_id in local_docs:
            print(f"Warning: duplicate uuid '{doc_id}' in source data; keeping the latter row.", flush=True)

        # Create a metadata dict that includes the answer, the original question
        # text, and the stable doc_id so both Pinecone and BM25 read the same id.
        metadata = {
            "answer": answer,
            "text": question,
            "doc_id": doc_id,
        }
        local_docs[doc_id] = Document(page_content=question, metadata=metadata)

    print(f"Loaded {len(local_docs)} unique Q&A pairs from all sources (skipped {duplicates_skipped} duplicates).", flush=True)

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
            print(f"Progress: {i}/{len(ids_to_upsert)} ({i*100//len(ids_to_upsert)}%) - Processing batch of {len(batch_ids)} documents...", flush=True)

            # Batch embed all documents at once instead of one-by-one
            batch_texts = [local_docs[doc_id].page_content for doc_id in batch_ids]
            batch_embeddings = embeddings.embed_documents(batch_texts)

            vectors_to_upsert = []
            for doc_id, embedding in zip(batch_ids, batch_embeddings):
                doc = local_docs[doc_id]
                vectors_to_upsert.append({"id": doc_id, "values": embedding, "metadata": doc.metadata})

            index.upsert(vectors=vectors_to_upsert)

        print(f"Progress: {len(ids_to_upsert)}/{len(ids_to_upsert)} (100%) - All documents upserted!", flush=True)
    else:
        print("No new or modified documents to upsert.", flush=True)

    # TODO: 未來可能需要調整此策略
    # 目前當 Pinecone 有但本地沒有時會直接刪除，可能需要改為保留或歸檔
    # Current strategy: Delete vectors in Pinecone that don't exist locally
    # Future consideration: May need to preserve or archive instead of deleting
    if ids_to_delete:
        print(f"Deleting {len(ids_to_delete)} outdated documents...", flush=True)
        index.delete(ids=ids_to_delete)
    else:
        print("No documents to delete.", flush=True)

    final_count = len(local_ids)
    print(f"Sync complete. Total vectors: {final_count}. Upserted: {len(ids_to_upsert)}, Deleted: {len(ids_to_delete)}.", flush=True)

    # Rebuild BM25 index so it stays consistent with the vector store
    try:
        bm25 = BM25Index()
        bm25.build_from_documents(list(local_docs.values()))
        print(f"BM25 index rebuilt with {bm25.corpus_size} documents.", flush=True)
    except Exception as bm25_err:
        print(f"Warning: BM25 index rebuild failed ({bm25_err}). Vector sync remains intact.", flush=True)

    return {"status": "success", "message": f"Sync complete. Upserted: {len(ids_to_upsert)}, Deleted: {len(ids_to_delete)}. Total vectors: {final_count}."}


if __name__ == '__main__':
    import sys
    try:
        result = sync_vector_store()
        print(result, flush=True)
    except Exception as e:
        # Exit non-zero so the Cloud Run Job execution is recorded as FAILED
        # (and the UI poll_status reports failure) instead of a false SUCCEEDED.
        print(f"An unexpected error occurred: {e}", flush=True)
        sys.exit(1)
