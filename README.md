# Carbon Assistant & BOM Mapping Tool

A containerized web app with a RAG chatbot for Q&A and a smart BOM header mapping tool, built with FastAPI, Gradio, and Docker.

---

## ✨ Features

- **Conversational AI**: An advanced RAG chatbot that answers questions about a carbon management system, based on a knowledge base built from user manuals.
- **Multi-Turn Conversation**: Intelligent query rewriting and conversation history context, enabling natural follow-up questions like "4.2 呢？" after discussing "類別 4.1 排放".
- **Intent Classification**: Intelligent filtering that identifies off-topic questions before retrieval, saving costs and improving user experience with helpful guidance.
- **Streaming Responses**: The chatbot provides answers token-by-token, offering a real-time, interactive user experience.
- **Intelligent BOM Mapping**: A hybrid tool that uses a combination of rule-based matching, fuzzy string matching, and Large Language Models (LLM) to map BOM file headers to a standardized format.
- **Asynchronous Task Processing**: Utilizes **Redis Queue (RQ)** to manage heavy background tasks (like knowledge base synchronization), ensuring the web UI remains responsive at all times.
- **Multi-Format File Handling**: The knowledge base can be updated by uploading various file formats, including `.pdf`, `.xlsx`, and `.csv`.
- **Gradio Modern Web UI**: A clean, user-friendly interface for seamless interaction with the AI assistant and mapping tools.
- **Prompt Caching**: Semantic similarity caching (powered by Redis) that reduces API costs and improves response times by up to 80% for similar questions.


---

## 💡 Key Technical Highlights

- **Intent Classification Layer**: Uses **Gemini 2.5 Flash** to filter off-topic questions before retrieval (95%+ accuracy, <400ms latency, ~$0.0001/query).
- **Query Rewriting**: Automatically rewrites follow-up questions into standalone queries using LLM, enabling accurate retrieval for contextual multi-turn conversations.
- **Response Guardrail**: Multi-layer protection including input injection detection, BGE Reranker-based context similarity validation, and PII/injection scanning on responses.
- **Asynchronous Task Queue**: Uses **Redis Queue (RQ)** to run heavy tasks (e.g., knowledge base sync) in a background `worker` process, ensuring a responsive UI.
- **Hybrid BOM Mapping**: A 3-stage process (rules, fuzzy matching, and LLM-based classification) provides highly accurate header mapping.
- **Efficient Vector Sync**: Performs an incremental sync with **Pinecone**, only updating new or changed data instead of full rebuilds.
- **Dual AI Model Strategy**: Uses **OpenAI** for high-quality embeddings and **Google Gemini 2.5 Flash** for fast, versatile chat and data analysis.
- **Semantic Cache Layer**: Implemented a cosine-similarity based cache in Redis to intercept similar questions, significantly decreasing latency and token consumption.
- **Conversation Logging**: All conversations are persisted to **MongoDB** with metadata including response source, intent classification, and cache hit status.
- **Observability**: Full tracing via **LangSmith** across the RAG pipeline (intent classification, reranking, retrieval, and generation).


---

## 🛠️ Core Technology Stack

- **Backend**: FastAPI
- **Web UI**: Gradio
- **Vector Database**: Pinecone
- **AI Models**: OpenAI, Google Gemini
- **Reranking**: BGE Reranker v2-m3 (HuggingFace Cross-Encoder)
- **Observability**: LangSmith
- **Conversation Storage**: MongoDB
- **Task Queue**: Redis Queue (RQ)
- **Message Broker**: Redis
- **Containerization**: Docker & Docker Compose

---

## 🏗️ System Architecture

The system uses a decoupled architecture orchestrated by Docker Compose:

- **`web`**: FastAPI/Gradio UI. Enqueues jobs to Redis.
- **`redis`**: Message broker holding the task queue and semantic cache.
- **`worker`**: Background RQ worker that executes heavy tasks.
- **`mongodb`**: Conversation logging database.
- **`mongo-express`**: Web-based MongoDB admin GUI (port 8081).
- **`redis-insight`**: Redis GUI for inspecting the prompt cache (port 8001).

👉 **[查看完整 RAG 架構圖](architecture.md)**

---

## 🚀 Getting Started

### Prerequisites

- Docker and Docker Compose
- Git

### 1. Environment Setup

Clone the repository and create a `.env` file in the project root:

```
OPENAI_API_KEY="your_openai_api_key_here"
PINECONE_API_KEY="your_pinecone_api_key_here"
GOOGLE_API_KEY="your_google_api_key_here"

# Optional: LangSmith observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY="your_langchain_api_key_here"
LANGCHAIN_PROJECT="carbon-assistant"
```

### 2. Launch the Application

```bash
docker-compose up --build
```

### 3. Access the Tools

- **Main Application**: [http://localhost:8000](http://localhost:8000)
- **Redis GUI (RedisInsight)**: [http://localhost:8001](http://localhost:8001) - Use this to inspect the prompt cache.
- **MongoDB GUI (Mongo Express)**: [http://localhost:8081](http://localhost:8081) - Use this to inspect conversation logs.

---

## 🕹️ UI Demo

The UI has three tabs: RAG Chatbot, BOM Header Mapper, and Admin: Upload Manual.

![Chatbot UI Demo](assets/chatbot_screenshot.jpeg)
![BOM Mapper UI Demo](assets/Bom_mapper.jpeg)
![Admin Upload UI Demo](assets/user_manual_upload.jpeg)

---

## 📚 Documentation

Detailed development records and design decisions:

- [Implementation Plan](docs/development/prompt-cache-implementation-plan.md)
- [Feature Walkthrough & Verification](docs/development/prompt-cache-walkthrough.md)
- [Development Task Checklist](docs/development/prompt-cache-task.md)