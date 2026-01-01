#!/usr/bin/env python3
"""
Script to inspect Pinecone vector database structure.

This script connects to Pinecone and displays:
- Index configuration
- Statistics
- Sample vectors with metadata
- Data structure analysis
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone
import json
from src.config import PINECONE_INDEX_NAME


def inspect_index_info(pc: Pinecone, index_name: str):
    """Display index configuration and statistics."""
    print("\n" + "="*80)
    print("INDEX CONFIGURATION")
    print("="*80)

    # Get index info
    indexes = pc.list_indexes()
    print(f"\n📋 Available Indexes:")
    for idx in indexes:
        print(f"   - {idx.name}")

    print(f"\n🎯 Current Index: {index_name}")

    # Get index description
    index = pc.Index(index_name)
    stats = index.describe_index_stats()

    print(f"\n📊 Index Statistics:")
    print(f"   Total vectors: {stats.total_vector_count:,}")
    print(f"   Dimension: {stats.dimension}")
    print(f"   Index fullness: {stats.index_fullness}")

    if hasattr(stats, 'namespaces') and stats.namespaces:
        print(f"\n📁 Namespaces:")
        for ns_name, ns_stats in stats.namespaces.items():
            ns_display = ns_name if ns_name else "(default)"
            print(f"   {ns_display}: {ns_stats.vector_count:,} vectors")

    return index, stats


def fetch_sample_vectors(index, num_samples: int = 5):
    """Fetch sample vectors to inspect metadata structure."""
    print("\n" + "="*80)
    print(f"SAMPLE VECTORS (First {num_samples})")
    print("="*80)

    # List vector IDs
    print("\n🔍 Fetching vector IDs...")
    vector_ids = []
    for ids_batch in index.list():
        vector_ids.extend(ids_batch)
        if len(vector_ids) >= num_samples:
            break

    if not vector_ids:
        print("⚠️  No vectors found in index!")
        return []

    print(f"✅ Found {len(vector_ids)} vector IDs")

    # Fetch sample vectors
    sample_ids = vector_ids[:num_samples]
    print(f"\n📦 Fetching {len(sample_ids)} sample vectors...")

    fetch_response = index.fetch(ids=sample_ids)
    vectors = fetch_response.vectors

    return vectors


def analyze_metadata_structure(vectors: dict):
    """Analyze and display metadata structure."""
    print("\n" + "="*80)
    print("METADATA STRUCTURE ANALYSIS")
    print("="*80)

    if not vectors:
        print("⚠️  No vectors to analyze!")
        return

    # Collect all metadata fields
    all_fields = set()
    field_types = {}
    field_samples = {}

    for vec_id, vec_data in vectors.items():
        if hasattr(vec_data, 'metadata') and vec_data.metadata:
            for field, value in vec_data.metadata.items():
                all_fields.add(field)

                # Track field types
                value_type = type(value).__name__
                if field not in field_types:
                    field_types[field] = set()
                field_types[field].add(value_type)

                # Store sample values
                if field not in field_samples:
                    field_samples[field] = []
                if len(field_samples[field]) < 3:  # Keep up to 3 samples
                    field_samples[field].append(value)

    print(f"\n📋 Metadata Fields Found: {len(all_fields)}")
    print("\nField Details:")
    print("-" * 80)

    for field in sorted(all_fields):
        types = ", ".join(sorted(field_types[field]))
        print(f"\n   {field}:")
        print(f"      Type(s): {types}")

        if field in field_samples and field_samples[field]:
            print(f"      Sample values:")
            for i, sample in enumerate(field_samples[field], 1):
                sample_str = str(sample)
                if len(sample_str) > 100:
                    sample_str = sample_str[:100] + "..."
                print(f"         {i}. {sample_str}")


def display_sample_vectors(vectors: dict, num_display: int = 3):
    """Display detailed information for sample vectors."""
    print("\n" + "="*80)
    print(f"DETAILED SAMPLE VECTORS (First {num_display})")
    print("="*80)

    for i, (vec_id, vec_data) in enumerate(list(vectors.items())[:num_display], 1):
        print(f"\n{'─'*80}")
        print(f"Vector #{i}")
        print(f"{'─'*80}")
        print(f"ID: {vec_id}")

        # Display values info (truncated)
        if hasattr(vec_data, 'values') and vec_data.values:
            values_preview = vec_data.values[:5]
            print(f"Values: [{', '.join(f'{v:.4f}' for v in values_preview)}, ...] (dimension: {len(vec_data.values)})")

        # Display metadata
        if hasattr(vec_data, 'metadata') and vec_data.metadata:
            print(f"\nMetadata:")
            for key, value in vec_data.metadata.items():
                value_str = str(value)
                if len(value_str) > 200:
                    value_str = value_str[:200] + "..."
                print(f"   {key}: {value_str}")
        else:
            print("\nMetadata: (none)")


def analyze_vector_ids(index):
    """Analyze vector ID patterns."""
    print("\n" + "="*80)
    print("VECTOR ID PATTERN ANALYSIS")
    print("="*80)

    print("\n🔍 Collecting vector IDs...")
    vector_ids = []
    for ids_batch in index.list():
        vector_ids.extend(ids_batch)
        if len(vector_ids) >= 100:  # Limit to first 100 for analysis
            break

    print(f"✅ Collected {len(vector_ids)} IDs for analysis")

    # Analyze ID patterns
    id_prefixes = {}
    for vid in vector_ids[:20]:  # Analyze first 20
        parts = vid.split('_')
        if len(parts) > 0:
            prefix = parts[0]
            id_prefixes[prefix] = id_prefixes.get(prefix, 0) + 1

    print(f"\n📋 ID Patterns (sample of 20):")
    for prefix, count in sorted(id_prefixes.items(), key=lambda x: x[1], reverse=True):
        print(f"   {prefix}_*: {count} IDs")

    print(f"\n🔢 Sample IDs:")
    for vid in vector_ids[:10]:
        print(f"   {vid}")


def generate_report(index_name: str, stats, vectors: dict):
    """Generate a comprehensive report."""
    report = f"""# Pinecone Vector Database Structure Report

**Index Name**: {index_name}
**Generated**: {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}

## Index Statistics

- **Total Vectors**: {stats.total_vector_count:,}
- **Dimension**: {stats.dimension}
- **Index Fullness**: {stats.index_fullness}

## Data Structure

### Vector ID Format

Based on the codebase (`src/build_vector_store.py:96`), vector IDs are generated using:

```python
doc_id = f"qa_{{abs(hash(question))}}_{{i}}"
```

Pattern: `qa_<hash>_<index>`

Example: `qa_1234567890_0`

### Metadata Schema

Each vector contains the following metadata fields:

"""

    # Add metadata fields
    if vectors:
        all_fields = set()
        for vec_data in vectors.values():
            if hasattr(vec_data, 'metadata') and vec_data.metadata:
                all_fields.update(vec_data.metadata.keys())

        report += "\n| Field | Type | Description |\n"
        report += "|-------|------|-------------|\n"

        for field in sorted(all_fields):
            # Get sample value to determine type
            sample_val = None
            for vec_data in vectors.values():
                if hasattr(vec_data, 'metadata') and vec_data.metadata and field in vec_data.metadata:
                    sample_val = vec_data.metadata[field]
                    break

            field_type = type(sample_val).__name__ if sample_val else "unknown"

            # Add description based on field name
            descriptions = {
                "answer": "The answer text corresponding to the question",
                "text": "The original question text",
            }
            desc = descriptions.get(field, "")

            report += f"| `{field}` | {field_type} | {desc} |\n"

    report += """
## Vector Storage Format

Each vector in Pinecone contains:

1. **ID**: Unique identifier (e.g., `qa_1234567890_0`)
2. **Values**: 1536-dimensional embedding vector (from OpenAI)
3. **Metadata**: Dictionary containing:
   - `answer`: The answer text
   - `text`: The question text

## Usage in RAG Pipeline

1. **Ingestion** (`build_vector_store.py`):
   - Questions are embedded using OpenAI Embeddings
   - Stored with question hash as part of ID
   - Metadata includes both question and answer

2. **Retrieval** (`app.py`):
   - User query is embedded
   - Top 10 similar vectors retrieved (k=10)
   - Reranked to top 3 using BGE Reranker
   - Context used for LLM generation

## Code References

- Vector store sync: `src/build_vector_store.py:59-146`
- Metadata structure: `src/build_vector_store.py:98-102`
- Retrieval: `src/app.py` (chat_stream function)
"""

    return report


def main():
    """Main inspection execution."""
    # Load environment variables
    load_dotenv()

    print("\n🔍 PINECONE VECTOR DATABASE STRUCTURE INSPECTOR")
    print("="*80)

    # Initialize Pinecone
    print("\n🔌 Connecting to Pinecone...")
    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("❌ Error: PINECONE_API_KEY not found in environment variables!")
        return

    pc = Pinecone(api_key=api_key)
    print("✅ Connected to Pinecone")

    # Inspect index
    index, stats = inspect_index_info(pc, PINECONE_INDEX_NAME)

    # Analyze vector ID patterns
    analyze_vector_ids(index)

    # Fetch sample vectors
    vectors = fetch_sample_vectors(index, num_samples=10)

    if vectors:
        # Analyze metadata structure
        analyze_metadata_structure(vectors)

        # Display detailed samples
        display_sample_vectors(vectors, num_display=3)

        # Generate report
        print("\n" + "="*80)
        print("GENERATING REPORT")
        print("="*80)

        report = generate_report(PINECONE_INDEX_NAME, stats, vectors)

        with open("pinecone_structure_report.md", "w") as f:
            f.write(report)

        print("\n✅ Report saved to pinecone_structure_report.md")

    print("\n" + "="*80)
    print("🎉 Inspection complete!")
    print("="*80)


if __name__ == "__main__":
    main()
