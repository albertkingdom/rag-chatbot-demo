
import os
import json
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn
import shutil

# Import the classifier function from our other script
from bom_mapper import classify_bom_headers

# Initialize the FastAPI app
app = FastAPI()

# Mount a directory for static files (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates to serve the HTML page
templates = Jinja2Templates(directory="templates")

# --- Production-Grade RAG Setup ---
# This section initializes the components needed for the RAG pipeline.
# It uses environment variables for API keys and configuration.

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate

# Ensure environment variables are set:
# OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_ENVIRONMENT
PINECONE_INDEX_NAME = "carbon-assistant-qa-index"

# 1. Initialize Embeddings and LLM
embeddings = OpenAIEmbeddings()
llm = ChatOpenAI(model_name="gpt-3.5-turbo", temperature=0)

# 2. Initialize Pinecone Vector Store as Retriever
# This assumes the index has already been built and populated by `build_vector_store.py`
vectorstore = PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=embeddings)
retriever = vectorstore.as_retriever()

# 3. Define a custom prompt to guide the LLM
prompt_template = """
You are a professional assistant for a carbon management system. 
Use the following pieces of context (retrieved from a knowledge base) to answer the user's question.
If you don't know the answer from the context, just say that you don't know, don't try to make up an answer.
Keep the answer concise and helpful.

Context:
{context}

Question:
{question}

Helpful Answer:
"""
QA_PROMPT = PromptTemplate(
    template=prompt_template, input_variables=["context", "question"]
)

# 4. Create the RetrievalQA chain
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    retriever=retriever,
    chain_type_kwargs={"prompt": QA_PROMPT},
    return_source_documents=True # Optionally return source documents for verification
)

# --- API Endpoints ---

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
        # Use the RAG chain to get an answer
        result = qa_chain.invoke({"query": user_message})
        
        # The main answer
        response_text = result.get("result", "Sorry, I had trouble generating a response.")
        
        # Optional: You can also inspect the source documents
        # source_documents = result.get("source_documents", [])
        # if source_documents:
        #     print("Sources:", [doc.metadata for doc in source_documents])

    except Exception as e:
        print(f"Error during RAG chain invocation: {e}")
        response_text = "An error occurred while processing your question. Please ensure your API keys and environment variables are set correctly."

    return JSONResponse(content={"response": response_text})

@app.post("/map_bom")
async def map_bom(file: UploadFile = File(...)):
    """Accepts a file upload, saves it temporarily, and uses the bom_mapper to classify it."""
    # Create a temporary directory if it doesn't exist
    temp_dir = "temp_files"
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    temp_file_path = os.path.join(temp_dir, file.filename)

    # Save the uploaded file
    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Call the classification function
        result = classify_bom_headers(temp_file_path)

    except Exception as e:
        return JSONResponse(content={"error": f"An error occurred: {e}"}, status_code=500)
    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

    if "error" in result:
        return JSONResponse(content=result, status_code=400)
        
    return JSONResponse(content=result)

# This part is for local development and allows running the app directly
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
