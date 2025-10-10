import os
import json
import shutil
import uvicorn
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain.prompts import PromptTemplate
from langchain.schema import StrOutputParser
from langchain.schema.runnable import RunnablePassthrough

# Import the classifier function from our other script
from bom_mapper import classify_bom_headers
from build_vector_store import sync_vector_store

# --- Base Directory --- #
# This helps in creating absolute paths for templates and static files
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()

# Mount static files and templates using absolute paths
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# --- Production-Grade RAG Setup --- #
PINECONE_INDEX_NAME = "carbon-assistant-qa-index"

# 1. Initialize Embeddings and LLM
embeddings = OpenAIEmbeddings()
llm = ChatOpenAI(model_name="gpt-4o", temperature=0)

# 2. Initialize Pinecone Vector Store as Retriever
vectorstore = PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=embeddings)
retriever = vectorstore.as_retriever()

# 3. Define a custom prompt to guide the LLM
prompt_template = """You are a professional assistant for a carbon management system. 
Use the following piece of context, which is the answer to a frequently asked question, to answer the user's question.
If the context is not relevant, just say that you don't know, don't try to make up an answer.
Keep the answer concise and helpful.

Context:
{context}

Question:
{question}

Helpful Answer:"""
QA_PROMPT = PromptTemplate(
    template=prompt_template, input_variables=["context", "question"]
)

# 4. Create a custom RAG chain to format the context correctly
def format_docs(docs):
    return "\n\n".join(doc.metadata.get('answer', '') for doc in docs)

# A new function to print documents for debugging
def log_docs(docs):
    print("--- Retrieved Documents ---")
    for doc in docs:
        print(f"  - Question: {doc.page_content}")
        print(f"    Answer: {doc.metadata.get('answer', 'N/A')[:50]}...") # Print first 50 chars of answer
    print("---------------------------")
    return docs

rag_chain = (
    {"context": retriever | RunnablePassthrough(log_docs) | format_docs, "question": RunnablePassthrough()}
    | QA_PROMPT
    | llm
    | StrOutputParser()
)

# --- API Endpoints --- #

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the main HTML page."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
async def chat(request: Request):
    """Handles chat logic using a production-grade RAG pipeline."""
    data = await request.json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return JSONResponse(content={"response": "Please ask a question."}, status_code=400)

    try:
        response_text = rag_chain.invoke(user_message)
    except Exception as e:
        print(f"Error during RAG chain invocation: {e}")
        response_text = "An error occurred while processing your question. Please ensure your API keys and environment variables are set correctly."

    return JSONResponse(content={"response": response_text})

@app.post("/map_bom")
async def map_bom(file: UploadFile = File(...)):
    """Accepts a file upload, saves it temporarily, and uses the bom_mapper to classify it."""
    temp_dir = BASE_DIR / "temp_files"
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    temp_file_path = temp_dir / file.filename

    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        result = classify_bom_headers(str(temp_file_path))
    except Exception as e:
        return JSONResponse(content={"error": f"An error occurred: {e}"}, status_code=500)
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

    if "error" in result:
        return JSONResponse(content=result, status_code=400)
        
    return JSONResponse(content=result)


@app.post("/admin/sync-knowledge-base")
async def sync_kb():
    """Triggers the synchronization of the knowledge base with Pinecone."""
    try:
        result = sync_vector_store()
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)