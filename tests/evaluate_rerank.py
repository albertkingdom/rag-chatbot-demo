import json
import os
from langchain_core.documents import Document
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

def evaluate_rerank():
    # Set model cache directory to ./models
    model_dir = os.path.join(os.getcwd(), "models")
    os.makedirs(model_dir, exist_ok=True)

    # Load evaluation data
    data_path = "tests/rerank_eval_data.json"
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found.")
        return

    with open(data_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # Initialize HuggingFace CrossEncoder via langchain_community
    print(f"Initializing BGE Reranker (BAAI/bge-reranker-v2-m3)...")
    print(f"Models will be stored in: {model_dir}")
    
    model = HuggingFaceCrossEncoder(
        model_name="BAAI/bge-reranker-v2-m3",
        model_kwargs={"device": "cpu", "cache_folder": model_dir}
    )
    reranker = CrossEncoderReranker(model=model, top_n=3)

    for i, case in enumerate(cases):
        question = case["question"]
        print(f"\n--- Case {i+1}: {question} ---")

        # Mix relevant and irrelevant docs
        docs = [Document(page_content=txt) for txt in (case["irrelevant_docs"] + case["relevant_docs"])]
        
        print("\n[Before Rerank]:")
        for j, doc in enumerate(docs):
            status = "RELEVANT" if doc.page_content in case["relevant_docs"] else "irrelevant"
            print(f"{j+1}. [{status}] {doc.page_content[:50]}...")

        # Perform Reranking
        print("\nRunning Rerank...")
        compressed_docs = reranker.compress_documents(docs, question)

        print("\n[After Rerank] (Top 3 results):")
        for j, doc in enumerate(compressed_docs):
            status = "RELEVANT" if doc.page_content in case["relevant_docs"] else "irrelevant"
            relevance_score = doc.metadata.get("relevance_score", "N/A")
            print(f"{j+1}. [{status}] (Score: {relevance_score}) {doc.page_content[:50]}...")

        top_contents = [d.page_content for d in compressed_docs]
        success = any(rel in top_contents for rel in case["relevant_docs"])
        print(f"\nResult: {'SUCCESS ✅' if success else 'FAILURE ❌'}")

if __name__ == "__main__":
    try:
        evaluate_rerank()
    except Exception as e:
        import traceback
        traceback.print_exc()
