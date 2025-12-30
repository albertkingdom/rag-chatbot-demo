# Cedars Carbon Assistant - RAG Architecture

## System Overview

```mermaid
flowchart TB
    subgraph UI["Frontend (Gradio UI)"]
        Chatbot[RAG Chatbot]
        Upload[Upload Manual]
    end

    subgraph Web["Web Service (FastAPI)"]
        ChatStream[chat_stream]
        SyncJob[sync_vector_store]
    end

    subgraph Infra["Infrastructure"]
        Redis[(Redis<br/>Semantic Cache)]
        Worker[RQ Worker]
    end

    subgraph External["External Services"]
        OpenAI[OpenAI<br/>Embeddings]
        Pinecone[(Pinecone<br/>Vector DB)]
        HuggingFace[BGE Reranker<br/>v2-m3]
        Gemini[Gemini 2.5 Flash]
        LangSmith[LangSmith]
    end

    Chatbot --> ChatStream
    Upload --> SyncJob

    ChatStream --> Redis
    ChatStream --> OpenAI
    ChatStream --> Pinecone
    ChatStream --> HuggingFace
    ChatStream --> Gemini
    ChatStream -.-> LangSmith

    SyncJob --> Worker
    Worker --> OpenAI
    Worker --> Pinecone
    Worker -.-> LangSmith
```

## RAG Pipeline

```mermaid
flowchart LR
    Query[User Query]

    subgraph Embedding
        Embed[OpenAI Embedding<br/>1536-dim]
    end

    subgraph Cache["Semantic Cache"]
        CacheCheck{Cache Hit?<br/>similarity > 0.85}
        CacheStore[(Store)]
    end

    subgraph Retrieval["Vector Retrieval"]
        Pinecone[(Pinecone<br/>k=10)]
    end

    subgraph Reranking
        BGE[BGE Reranker v2-m3]
        Top3[Top 3 Documents]
    end

    subgraph Generation
        Context[Format Context]
        Gemini[Gemini 2.5 Flash]
    end

    Response[Stream Response]

    Query --> Embed
    Embed --> CacheCheck
    CacheCheck -->|HIT| Response
    CacheCheck -->|MISS| Pinecone
    Pinecone --> BGE
    BGE --> Top3
    Top3 --> Context
    Context --> Gemini
    Gemini --> CacheStore
    CacheStore --> Response
```

## Document Ingestion

```mermaid
flowchart TB
    subgraph Upload["File Upload"]
        PDF[PDF]
        XLSX[Excel]
        CSV[CSV]
    end

    subgraph Parse["Parsing"]
        Extract[Extract Q&A Pairs]
    end

    subgraph Process["Processing"]
        Hash[Generate Doc ID]
        Compare{Compare with<br/>Existing}
    end

    subgraph Embed["Embedding"]
        OpenAI[OpenAI API]
    end

    subgraph Sync["Sync"]
        Upsert[Batch Upsert]
        Delete[Delete Outdated]
    end

    Pinecone[(Pinecone)]

    PDF --> Extract
    XLSX --> Extract
    CSV --> Extract
    Extract --> Hash
    Hash --> Compare
    Compare -->|New| OpenAI
    Compare -->|Outdated| Delete
    OpenAI --> Upsert
    Upsert --> Pinecone
    Delete --> Pinecone
```

## Sequence Diagram

```mermaid
sequenceDiagram
    participant U as User
    participant F as FastAPI
    participant R as Redis
    participant O as OpenAI
    participant P as Pinecone
    participant B as BGE Reranker
    participant G as Gemini

    U->>F: Submit Question
    F->>O: Generate Embedding
    O-->>F: 1536-dim Vector
    F->>R: Check Cache

    alt Cache Hit
        R-->>F: Cached Response
        F-->>U: Return Answer
    else Cache Miss
        F->>P: Query (k=10)
        P-->>F: 10 Candidates
        F->>B: Rerank
        B-->>F: Top 3 Docs
        F->>G: Generate Answer
        G-->>F: Streaming Response
        F->>R: Store in Cache
        F-->>U: Stream Answer
    end
```

## Key Parameters

| Component | Parameter | Value |
|-----------|-----------|-------|
| Vector Retrieval | k | 10 |
| Reranking | Top N | 3 |
| Semantic Cache | Threshold | 0.85 |
| Semantic Cache | TTL | 24 hours |
| Embedding | Dimensions | 1536 |
| LLM | Model | gemini-2.5-flash |
| LLM | Temperature | 0 |
