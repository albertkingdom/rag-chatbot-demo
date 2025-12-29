import os
import shutil
from pathlib import Path
import uvicorn
import gradio as gr
from fastapi import FastAPI
from typing import AsyncGenerator
import redis
from rq import Queue
from rq.job import Job
import time
import traceback
from langsmith import traceable

from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

from .bom_mapper import classify_bom_headers
from .build_vector_store import sync_vector_store
from .cache_service import PromptCacheService
from .config import PINECONE_INDEX_NAME, DATA_SOURCE_DIR, CACHE_ENABLED, CACHE_SIMILARITY_THRESHOLD

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
conn = redis.from_url(redis_url)
q = Queue(connection=conn)

# Singleton for BGE-Reranker model
_reranker = None

def get_reranker_model():
    """取得底層的 CrossEncoder 模型實體。"""
    global _reranker
    if _reranker is None:
        print("Initializing BGE-Reranker model (BAAI/bge-reranker-v2-m3)...")
        model_dir = os.path.join(os.getcwd(), "models")
        os.makedirs(model_dir, exist_ok=True)
        
        # Load the CrossEncoder model
        _reranker = HuggingFaceCrossEncoder(
            model_name="BAAI/bge-reranker-v2-m3",
            model_kwargs={"device": "cpu", "cache_folder": model_dir}
        )
        print("BGE-Reranker model initialized successfully.")
    return _reranker

@traceable(name="Rerank Analysis")
def rerank_analysis(docs_and_query):
    """手動執行重排序並將分數寫入 Metadata，解決套件版本不支援顯示分數的問題。"""
    docs = docs_and_query["docs"]
    query = docs_and_query["query"]
    
    if not docs:
        return {"docs": [], "rerank_scores": []}

    model = get_reranker_model()
    
    # 手動計算每個文件的分數
    scores = model.score([(query, doc.page_content) for doc in docs])
    
    # 將分數寫回 metadata 並排序
    for doc, score in zip(docs, scores):
        doc.metadata["relevance_score"] = float(score)
    
    # 根據分數排序並取 Top 3
    sorted_docs = sorted(docs, key=lambda x: x.metadata["relevance_score"], reverse=True)[:3]
    
    # 準備 LangSmith 摘要
    summary = []
    for i, doc in enumerate(sorted_docs):
        score = doc.metadata["relevance_score"]
        summary.append({
            "rank": i + 1,
            "score": f"{score:.4f}",
            "content": doc.page_content[:100] + "..."
        })
    
    return {"docs": sorted_docs, "rerank_scores": summary}


@traceable(name="Carbon Assistant Chat")
async def chat_stream(message: str, history: list) -> AsyncGenerator[str, None]:
    """Handles the entire RAG chain lifecycle for a single chat request with caching."""
    PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") # For OpenAI Embeddings
    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") # For Gemini Chat Model
    if not (PINECONE_API_KEY and OPENAI_API_KEY and GOOGLE_API_KEY):
        yield "Error: All required API keys are not configured on the server."
        return
    
    try:
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        
        # Generate embedding for the incoming question (needed for both cache and RAG)
        question_embedding = embeddings.embed_query(message)
        
        # Try to get cached response if cache is enabled
        if CACHE_ENABLED:
            cache_service = PromptCacheService(conn)
            cached_response = cache_service.get_cached_response(
                question_embedding, 
                threshold=CACHE_SIMILARITY_THRESHOLD
            )
            
            if cached_response:
                # Cache hit - return cached answer with indicator
                print(f"Cache hit! Similarity: {cached_response['similarity']:.3f}, Hit count: {cached_response['hit_count']}")
                cached_answer = cached_response["answer"]
                # Stream the cached response for consistent UI experience
                yield cached_answer
                return
        
        # Cache miss or cache disabled - proceed with normal RAG pipeline
        pc = Pinecone(api_key=PINECONE_API_KEY)
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, streaming=True, google_api_key=GOOGLE_API_KEY)
        vectorstore = PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=embeddings)
        
        # Base retriever to fetch more candidates (e.g., top 10) for reranking
        base_retriever = vectorstore.as_retriever(search_kwargs={"k": 10})
        
        prompt_template = """You are a professional assistant for a carbon management system. 
        Use the following piece of context, which is the answer to a frequently asked question, to answer the user's question.
        If the context is not relevant, just say that you don't know, don't try to make up an answer.
        Keep the answer concise and helpful.

        Context:
        {context}

        Question:
        {question}

        Helpful Answer:"""
        QA_PROMPT = PromptTemplate.from_template(prompt_template)
        @traceable(name="Prepare Context")
        def format_docs(outputs):
            # 從 rerank_analysis 的輸出字典中提取文件列表
            docs = outputs["docs"]
            return "\n\n".join(doc.metadata.get('answer', '') for doc in docs)
        
        # 建立檢索鏈：這會傳回 {"docs": [...], "query": "..."} 到下一個步驟
        retrieval_chain = (
            {"docs": base_retriever, "query": lambda x: x}
            | RunnableLambda(rerank_analysis) 
            | RunnableLambda(format_docs)
        )
        generation_chain = QA_PROMPT | llm | StrOutputParser()
        context = await retrieval_chain.ainvoke(message)
        full_response = ""
        async for chunk in generation_chain.astream({"context": context, "question": message}):
            full_response += chunk
            yield full_response
        
        # Store the generated response in cache
        if CACHE_ENABLED and full_response:
            cache_service.set_cached_response(
                question_embedding,
                message,
                full_response
            )
            print(f"Response cached for question: {message[:50]}...")
            
    except Exception as e:
        print(f"An error occurred during chat stream: {e}")
        yield f"An error occurred: {e}"

def bom_mapper_func(file):
    """
    Wrapper function for BOM mapping.
    Disables the button during execution and re-enables it upon completion.
    """
    if file is None:
        # Return updates for both outputs: a message for the JSON and no change for the button
        return {"error": "Please upload a file first."}, gr.update(interactive=True)
    
    # Disable button immediately
    yield {"status": "Processing..."}, gr.update(interactive=False)
    
    try:
        # Perform the actual mapping
        result = classify_bom_headers(file.name)
        # Return final result and re-enable the button
        yield result, gr.update(interactive=True)
    except Exception as e:
        traceback.print_exc()
        yield {"error": str(e)}, gr.update(interactive=True)

def upload_manual_func(file):
    """Saves the file and enqueues a sync job, returning the job ID."""
    if file is None:
        return "No file uploaded.", None

    save_dir = Path(DATA_SOURCE_DIR)
    os.makedirs(save_dir, exist_ok=True)
    save_path = save_dir / Path(file.name).name

    try:
        shutil.copy(file.name, save_path)
        job = q.enqueue(sync_vector_store, job_timeout='1h')
        return f"File uploaded. Sync job '{job.id}' enqueued.", job.id
    except Exception as e:
        return f"Error: {str(e)}", None


with gr.Blocks(theme=gr.themes.Soft(), title="Carbon Assistant App") as demo:
    gr.Markdown("<h1>Carbon Assistant & BOM Mapping Tool</h1>")

    with gr.Tab("RAG Chatbot"):
        gr.ChatInterface(
            chat_stream,
            chatbot=gr.Chatbot(height=500),
            textbox=gr.Textbox(placeholder="Ask me about the user manual...", container=False, scale=7),
            title="User Manual Q&A",
            description="Ask questions about the carbon management system.",
            examples=["What is the purpose of this system?", "How do I calculate carbon emissions?"],
        )

    with gr.Tab("BOM Header Mapper"):
        with gr.Row():
            bom_input = gr.File(label="Upload BOM file (.csv)")
            bom_output = gr.JSON(label="Mapping Result")
        bom_button = gr.Button("Map Headers")
        
        # The click event now updates both the JSON output and the button itself
        bom_button.click(
            bom_mapper_func, 
            inputs=bom_input, 
            outputs=[bom_output, bom_button]
        )

    with gr.Tab("Admin: Upload Manual"):
        job_id_state = gr.State(None)

        with gr.Row():
            manual_input = gr.File(label="Upload User Manual (.pdf, .xlsx, .csv)")
            with gr.Column():
                manual_output = gr.Textbox(label="Upload Status", interactive=False, lines=2, max_lines=4)
                sync_status_output = gr.Textbox(label="Sync Process Status", interactive=False, lines=2, max_lines=4)
        
        manual_button = gr.Button("Upload and Enqueue Sync")

        def poll_status(job_id):
            """A generator function that polls the job status and yields updates."""
            if not job_id:
                yield "Job ID not found. Please upload again."
                return

            while True:
                try:
                    job = Job.fetch(job_id, connection=conn)
                    status = job.get_status(refresh=True)
                    
                    status_map = {
                        'queued': f"Job {job.id}: 已進入佇列，正在等待執行...",
                        'started': f"Job {job.id}: 任務執行中，系統正在處理您的文件...",
                        'finished': f"Job {job.id}: 同步成功！知識庫已更新。",
                        'failed': f"Job {job.id}: 任務失敗！請檢查後台日誌。",
                    }
                    
                    message = status_map.get(status, f"Job {job.id}: 未知狀態 ({status})")
                    yield message

                    if status in ['finished', 'failed', 'canceled', 'stopped']:
                        break
                    
                    time.sleep(2)

                except Exception as e:
                    yield f"無法獲取任務狀態 {job_id}: {e}"
                    break

        upload_event = manual_button.click(
            upload_manual_func, 
            inputs=manual_input, 
            outputs=[manual_output, job_id_state]
        )
        
        upload_event.then(
            poll_status,
            inputs=job_id_state,
            outputs=sync_status_output,
        )

app = FastAPI()
app = gr.mount_gradio_app(app, demo, path="/")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
