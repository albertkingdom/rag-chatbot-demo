import os
import shutil
from pathlib import Path
import uvicorn
import gradio as gr
from fastapi import FastAPI
from typing import AsyncGenerator
import redis
from rq import Queue

# Third-party Imports for RAG and BOM
from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

# Local Application Imports
from bom_mapper import classify_bom_headers
from build_vector_store import sync_vector_store
from config import PINECONE_INDEX_NAME, DATA_SOURCE_DIR

# --- RQ and Redis Connection ---
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
conn = redis.from_url(redis_url)
q = Queue(connection=conn)

# --- Gradio Interface Functions ---

async def chat_stream(message: str, history: list) -> AsyncGenerator[str, None]:
    """Handles the entire RAG chain lifecycle for a single chat request."""
    
    PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

    if not (PINECONE_API_KEY and OPENAI_API_KEY):
        yield "Error: API keys are not configured on the server."
        return

    try:
        pc = Pinecone(api_key=PINECONE_API_KEY)
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        llm = ChatOpenAI(model_name="gpt-4o-mini", temperature=0, streaming=True, openai_api_key=OPENAI_API_KEY)
        
        vectorstore = PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=embeddings)
        retriever = vectorstore.as_retriever()

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

        def format_docs(docs):
            return "\n\n".join(doc.metadata.get('answer', '') for doc in docs)

        retrieval_chain = retriever | format_docs
        generation_chain = QA_PROMPT | llm | StrOutputParser()

        context = await retrieval_chain.ainvoke(message)

        full_response = ""
        async for chunk in generation_chain.astream({"context": context, "question": message}):
            full_response += chunk
            yield full_response
            
    except Exception as e:
        print(f"An error occurred during chat stream: {e}")
        yield f"An error occurred: {e}"

def bom_mapper_func(file):
    """Wrapper function for BOM mapping to be used in Gradio."""
    if file is None:
        return None
    try:
        result = classify_bom_headers(file.name)
        return result
    except Exception as e:
        return {"error": str(e)}

def upload_manual_func(file):
    """Saves the file and enqueues a sync job using RQ."""
    if file is None:
        return "No file uploaded."
    
    save_dir = Path(DATA_SOURCE_DIR)
    os.makedirs(save_dir, exist_ok=True)
    save_path = save_dir / Path(file.name).name

    try:
        shutil.copy(file.name, save_path)
        # Enqueue the sync_vector_store function to be run by an RQ worker
        q.enqueue(sync_vector_store)
        return f"File '{Path(file.name).name}' uploaded. Sync job has been enqueued."
    except Exception as e:
        return f"Error: {str(e)}"

# --- Gradio UI Layout ---

with gr.Blocks(theme=gr.themes.Soft(), title="Carbon Assistant App") as demo:
    gr.Markdown("<h1>Carbon Assistant & BOM Mapping Tool</h1>")

    with gr.Tab("RAG Chatbot"):
        gr.ChatInterface(
            chat_stream,
            chatbot=gr.Chatbot(height=500, type='messages'),
            type='messages',
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
        bom_button.click(bom_mapper_func, inputs=bom_input, outputs=bom_output)

    with gr.Tab("Admin: Upload Manual"):
        with gr.Row():
            manual_input = gr.File(label="Upload User Manual (.pdf, .xlsx, .csv)")
            manual_output = gr.Textbox(label="Upload Status")
        manual_button = gr.Button("Upload and Enqueue Sync")
        manual_button.click(upload_manual_func, inputs=manual_input, outputs=manual_output)

# --- FastAPI Mounting ---

app = FastAPI()

# Mount the Gradio app
app = gr.mount_gradio_app(app, demo, path="/")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
