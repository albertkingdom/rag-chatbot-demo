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
import traceback
import asyncio
from langsmith import traceable

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

from .bom_mapper import classify_bom_headers
from .build_vector_store import sync_vector_store
from .cache_service import PromptCacheService
from .intent_classifier import IntentClassifier
from .conversation_db import get_conversation_db
from .guardrails import (
    detect_pii,
    detect_prompt_injection,
    get_guardrail_message,
)
from .config import PINECONE_INDEX_NAME, DATA_SOURCE_DIR, CACHE_ENABLED, CACHE_SIMILARITY_THRESHOLD, BM25_TOP_N, VECTOR_TOP_N, RRF_K, FUSION_TOP_M
from .bm25_index import BM25Index
from .hybrid_retriever import HybridRetriever
from .rerank_stage import rerank_candidates

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
conn = redis.from_url(redis_url)
q = Queue(connection=conn)

# Singleton for BGE-Reranker model
_reranker = None

def _get_optimal_device():
    """自動偵測最佳運算裝置（CUDA > MPS > CPU）。"""
    import torch
    if torch.cuda.is_available():
        device = "cuda"
        print(f"Using CUDA GPU: {torch.cuda.get_device_name(0)}")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        print("Using Apple Silicon MPS acceleration")
    else:
        device = "cpu"
        print("Using CPU")
    return device

def get_reranker_model():
    """取得底層的 CrossEncoder 模型實體。"""
    global _reranker
    if _reranker is None:
        print("Initializing BGE-Reranker model (BAAI/bge-reranker-v2-m3)...")
        model_dir = os.path.join(os.getcwd(), "models")
        os.makedirs(model_dir, exist_ok=True)

        device = _get_optimal_device()
        
        # Load the CrossEncoder model
        _reranker = HuggingFaceCrossEncoder(
            model_name="BAAI/bge-reranker-v2-m3",
            model_kwargs={"device": device, "cache_folder": model_dir}
        )
        print(f"BGE-Reranker model initialized successfully on {device}.")
    return _reranker

# Singleton for Intent Classifier
_intent_classifier = None

def get_intent_classifier():
    """取得 Intent Classifier 實例。"""
    global _intent_classifier
    if _intent_classifier is None:
        print("Initializing Intent Classifier...")
        OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
        if not OPENROUTER_API_KEY:
            print("Warning: OPENROUTER_API_KEY not found. Intent classification will be disabled.")
            return None
        _intent_classifier = IntentClassifier(openrouter_api_key=OPENROUTER_API_KEY)
        print("Intent Classifier initialized successfully.")
    return _intent_classifier

# Singletons for core RAG components
_embeddings = None
_llm = None
_vectorstore = None
_retrieval_chain = None
_generation_chain = None
_bm25_index = None
_hybrid_retriever = None

def get_embeddings():
    global _embeddings
    if _embeddings is None:
        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not configured")
        _embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    return _embeddings

def get_llm():
    global _llm
    if _llm is None:
        OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
        if not OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY not configured")
        _llm = ChatOpenAI(
            model="google/gemini-2.5-flash",
            temperature=0,
            streaming=True,
            openai_api_key=OPENROUTER_API_KEY,
            openai_api_base="https://openrouter.ai/api/v1",
        )
    return _llm

def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=get_embeddings())
    return _vectorstore

def get_bm25_index():
    global _bm25_index
    if _bm25_index is None:
        _bm25_index = BM25Index()
    return _bm25_index

def get_hybrid_retriever():
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever(
            bm25_index=get_bm25_index(),
            vector_store=get_vectorstore(),
        )
    return _hybrid_retriever

def get_retrieval_chain():
    global _retrieval_chain
    if _retrieval_chain is None:
        retriever = get_hybrid_retriever()

        async def _retrieve(query: str) -> dict:
            result = await retriever.retrieve(
                query,
                vector_top_n=VECTOR_TOP_N,
                bm25_top_n=BM25_TOP_N,
                rrf_k=RRF_K,
            )
            return {"query": query, **result}

        def _rerank(payload: dict) -> dict:
            query = payload["query"]
            candidates = payload["candidates"]
            fusion_metadata = payload["fusion_metadata"]
            ranked = rerank_candidates(query, candidates, get_reranker_model().score)
            formatted = _format_docs(ranked)
            return {
                **formatted,
                "docs": ranked["docs"],
                "rerank_scores": ranked["rerank_scores"],
                "fusion_metadata": fusion_metadata,
            }

        _retrieval_chain = RunnableLambda(_retrieve) | RunnableLambda(_rerank)
    return _retrieval_chain

def get_generation_chain():
    global _generation_chain
    if _generation_chain is None:
        _generation_chain = _QA_PROMPT | get_llm() | StrOutputParser()
    return _generation_chain

_QA_PROMPT = PromptTemplate.from_template(
    """You are a professional assistant for a carbon management system.
        Use the following piece of context, which is the answer to a frequently asked question, to answer the user's question.
        If the context is not relevant, just say that you don't know, don't try to make up an answer.
        Keep the answer concise and helpful.
        {history}

        Context:
        {context}

        Question:
        {question}

        Helpful Answer:"""
)

@traceable(name="Prepare Context")
def _format_docs(outputs):
    docs = outputs["docs"]
    contexts = [doc.metadata.get("answer", "") for doc in docs if doc.metadata.get("answer")]
    return {"context": "\n\n".join(contexts), "contexts": contexts}


def _parse_history_turns(history: list, max_turns: int = 3) -> list:
    turns = []
    i = 0
    while i < len(history) - 1:
        msg = history[i]
        next_msg = history[i + 1]
        if isinstance(msg, dict) and isinstance(next_msg, dict):
            if msg.get("role") == "user" and next_msg.get("role") == "assistant":
                turns.append((msg["content"], next_msg["content"]))
                i += 2
                continue
        i += 1
    return turns[-max_turns:] if turns else []


def format_history(history: list, max_turns: int = 3) -> str:
    if not history:
        return ""
    recent = _parse_history_turns(history, max_turns)
    if not recent:
        return ""
    lines = ["\n\n        Previous conversation:"]
    for user_msg, bot_msg in recent:
        lines.append(f"        User: {user_msg}")
        lines.append(f"        Assistant: {bot_msg}")
    return "\n".join(lines)


@traceable(name="Query Rewriting")
async def rewrite_query(message: str, history: list, max_turns: int = 3) -> str:
    if not history:
        return message
    
    recent = _parse_history_turns(history, max_turns)
    if not recent:
        return message
    
    history_text = "\n".join([f"User: {u}\nAssistant: {a}" for u, a in recent])
    
    prompt = f"""Given the conversation history and the follow-up question, rewrite the question into a standalone question that can be understood without the conversation context.
If the question is already complete and self-contained, return it as-is.

Conversation history:
{history_text}

Follow-up question: {message}

Rewritten standalone question:"""
    
    try:
        llm = get_llm()
        response = await llm.ainvoke(prompt)
        rewritten = response.content.strip()
        print(f"[Query Rewriting] Original: {message}")
        print(f"[Query Rewriting] Rewritten: {rewritten}")
        return rewritten
    except Exception as e:
        print(f"[Query Rewriting] ERROR: {e}")
        return message


@traceable(name="Carbon Assistant Chat")
async def chat_stream(message: str, history: list, request: gr.Request = None) -> AsyncGenerator[str, None]:
    """Handles the entire RAG chain lifecycle for a single chat request with caching."""
    session_id = request.session_hash if request else None
    full_response = ""
    intent_result = None
    try:
        # Step 0a: Input guardrail - block prompt injection before hitting LLM
        injection_hit, _ = detect_prompt_injection(message)
        if injection_hit:
            db = get_conversation_db()
            await db.async_save_conversation(
                user_question=message,
                assistant_response=get_guardrail_message(),
                session_id=session_id,
                response_source="guardrail",
                metadata={"injection_pattern": "input_injection"}
            )
            yield get_guardrail_message()
            return

        # Step 0b: Query Rewriting - rewrite follow-up questions into standalone queries
        rewritten_query = await rewrite_query(message, history)

        # Step 0c: Intent Classification - Filter out off-topic questions
        status_msg = "正在分析您的問題..."
        for i in range(1, len(status_msg) + 1):
            yield status_msg[:i]
            await asyncio.sleep(0.02)
            
        intent_classifier = get_intent_classifier()
        if intent_classifier:
            intent_result = await intent_classifier.classify(rewritten_query)
            print(f"Intent classification: {intent_result}")

            # If question is not relevant, return early with helpful message
            if not intent_result["relevant"] or intent_result["confidence"] < 0.7:
                off_topic_message = intent_classifier.get_off_topic_message()
                db = get_conversation_db()
                await db.async_save_conversation(
                    user_question=message,
                    assistant_response=off_topic_message,
                    session_id=session_id,
                    response_source="off_topic",
                    intent_classification=intent_result,
                )
                for i in range(1, len(off_topic_message) + 1):
                    yield off_topic_message[:i]
                    await asyncio.sleep(0.01)
                return
        
        yield "正在從知識庫檢索相關資訊..."
        embeddings = get_embeddings()
        
        # Generate embedding for the rewritten query (needed for both cache and RAG)
        question_embedding = await embeddings.aembed_query(rewritten_query)
        
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

                pii_hit, pii_type = detect_pii(cached_answer)
                injection_hit, injection_pattern = detect_prompt_injection(cached_answer)
                if pii_hit or injection_hit:
                    guardrail_message = get_guardrail_message()
                    print(
                        "[Guardrail] Blocked cached response:",
                        {
                            "pii": pii_type if pii_hit else None,
                            "injection": injection_pattern if injection_hit else None,
                            "cache_similarity": cached_response.get("similarity"),
                        },
                    )
                    db = get_conversation_db()
                    await db.async_save_conversation(
                        user_question=message,
                        assistant_response=guardrail_message,
                        session_id=session_id,
                        response_source="guardrail",
                        intent_classification=intent_result,
                        cache_hit=False,
                        metadata={
                            "pii_type": pii_type if pii_hit else None,
                            "injection_pattern": injection_pattern if injection_hit else None,
                            "cache_candidate": True,
                            "cache_similarity": cached_response.get("similarity"),
                        },
                    )
                    for i in range(1, len(guardrail_message) + 1):
                        yield guardrail_message[:i]
                        await asyncio.sleep(0.005)
                    return
                
                # Record the CACHED conversation to MongoDB
                db = get_conversation_db()
                await db.async_save_conversation(
                    user_question=message,
                    assistant_response=cached_answer,
                    session_id=session_id,
                    response_source="cache",
                    intent_classification=intent_result,
                    cache_hit=True,
                    cache_similarity=cached_response['similarity']
                )
                
                # Stream the cached response for consistent UI experience
                for i in range(1, len(cached_answer) + 1):
                    yield cached_answer[:i]
                    await asyncio.sleep(0.005)
                return
        
        # Cache miss or cache disabled - proceed with normal RAG pipeline
        yield "正在優化檢索結果 (Reranking)..."
        context_bundle = await get_retrieval_chain().ainvoke(rewritten_query)
        context = context_bundle.get("context", "")
        contexts = context_bundle.get("contexts", [])

        yield "正在生成回答..."
        history_text = format_history(history)
        async for chunk in get_generation_chain().astream({"context": context, "question": message, "history": history_text}):
            full_response += chunk

        pii_hit, pii_type = detect_pii(full_response)
        injection_hit, injection_pattern = detect_prompt_injection(full_response)

        if pii_hit or injection_hit:
            guardrail_message = get_guardrail_message()
            print(
                "[Guardrail] Blocked response:",
                {
                    "pii": pii_type if pii_hit else None,
                    "injection": injection_pattern if injection_hit else None,
                },
            )
            db = get_conversation_db()
            await db.async_save_conversation(
                user_question=message,
                assistant_response=guardrail_message,
                session_id=session_id,
                response_source="guardrail",
                intent_classification=intent_result,
                cache_hit=False,
                metadata={
                    "pii_type": pii_type if pii_hit else None,
                    "injection_pattern": injection_pattern if injection_hit else None,
                },
            )
            for i in range(1, len(guardrail_message) + 1):
                yield guardrail_message[:i]
                await asyncio.sleep(0.005)
            return

        for i in range(1, len(full_response) + 1):
            yield full_response[:i]
            await asyncio.sleep(0.005)
        
        # Store the generated response in cache
        if CACHE_ENABLED and full_response:
            cache_service.set_cached_response(
                question_embedding,
                message,
                full_response
            )
            print(f"Response cached for question: {message[:50]}...")
            
        # Record the RAG conversation to MongoDB
        db = get_conversation_db()
        await db.async_save_conversation(
            user_question=message,
            assistant_response=full_response,
            session_id=session_id,
            response_source="rag",
            intent_classification=intent_result,
            cache_hit=False
        )
            
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
            textbox=gr.Textbox(placeholder="詢問碳管理系統相關問題...", container=False, scale=7),
            title="碳管理系統智慧助手",
            description="基於操作手冊的 RAG 問答系統，支援多輪對話",
            examples=[
                "忘記密碼怎麼辦？",
                "如何在多廠區管理中切換邊界？",
                "什麼是固定式燃料源排放？",
                "報告和清冊的格式是什麼？",
                "系統有哪些角色？",
                "門檻值設定該如何填寫？",
            ],
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

        async def poll_status(job_id):
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
                    
                    await asyncio.sleep(1)

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
