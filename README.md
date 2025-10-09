
# Carbon Management Assistant Chatbot

This project is a web-based intelligent assistant designed for a carbon management system, fulfilling the requirements of the interview homework. It features a RAG-based chatbot for answering user manual questions and a utility to automatically map Bill of Materials (BOM) file headers to predefined system fields.

This application is built with Python, FastAPI, and a simple HTML/JavaScript frontend. The entire environment is containerized with Docker for consistency and ease of deployment.

---

## System Architecture

Below is the architecture diagram for the application. You can copy the code into a Mermaid-compatible viewer (like the Mermaid Live Editor) to see the visual diagram.

```mermaid
graph TD
    subgraph User Browser
        A[index.html] -- HTTP Request --> B{FastAPI Backend}
    end

    subgraph Docker Container
        B -- Serves --> A
        B -- Calls function --> C[bom_mapper.py]
        B -- Accesses --> D[Knowledge Base]

        subgraph FastAPI Backend
            B_ROOT["/" GET] --> B_HTML(Serve index.html)
            B_CHAT["/chat" POST] --> B_RAG(Simulated RAG Logic)
            B_MAP["/map_bom" POST] --> B_SAVE(Save Temp File)
        end

        B_SAVE --> C
        B_RAG --> D
    end

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
```

---

## How to Run the Application

**Prerequisites:**
- You must have Docker and Docker Compose installed and running on your system.
- You must have a `.env` file in the project root containing your `OPENAI_API_KEY` and `PINECONE_API_KEY`.

This project uses a two-step process to ensure the knowledge base is ready before the main application starts.

**Step 1: Build the Knowledge Base (One-time setup)**

Open your terminal, navigate to the project directory, and run the following command. This will start a temporary container to execute the `build_vector_store.py` script, which creates the Pinecone index and populates it with your user manual data.

```bash
# First, build the main docker image
docker-compose build

# Then, run the one-time script to setup the vector store
docker-compose run --rm web python build_vector_store.py
```

**Step 2: Start the Application**

Once Step 1 is complete and the knowledge base is built, you can start the main web application with the following command:

```bash
docker-compose up
```

**Step 2: Access the Application**

Open your web browser and navigate to:

[http://localhost:8000](http://localhost:8000)

You should now see and be able to interact with the Carbon Management Assistant web application.

---

## How to Use

1.  **Chat Assistant**:
    - Type a question in English or Chinese into the chat input box (e.g., "How do I log in?" or "如何重設密碼?").
    - Click "Send" or press Enter.
    - The assistant will respond based on its knowledge base.

2.  **BOM Header Mapper**:
    - Click the "Choose File" button and select a BOM file in `.csv` format (you can use the provided `test_bom.xlsx.csv`).
    - Click the "Map Headers" button.
    - The mapping results will be displayed in the results box below, showing how your file's headers correspond to the system's fields.
