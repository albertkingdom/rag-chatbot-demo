#!/usr/bin/env python3
"""
Benchmark script to compare sequential vs batch embedding performance.

This script tests the performance improvement of batch embedding API
over sequential embedding calls using the generated_user_manual.xlsx dataset.
"""

import time
import pandas as pd
from langchain_openai import OpenAIEmbeddings
import statistics


def load_qa_data(file_path: str) -> list[str]:
    """Load questions from Excel file."""
    df = pd.read_excel(file_path)
    questions = df['question'].tolist()
    return questions


def benchmark_sequential_embedding(questions: list[str], embeddings: OpenAIEmbeddings) -> dict:
    """Benchmark sequential embedding (old approach)."""
    print("\n" + "="*80)
    print("BENCHMARK 1: Sequential Embedding (OLD)")
    print("="*80)
    print(f"Processing {len(questions)} questions...")

    start_time = time.time()
    embedded_questions = []

    for i, question in enumerate(questions):
        if i % 10 == 0:
            print(f"Progress: {i}/{len(questions)} ({i*100//len(questions)}%)", flush=True)
        embedding = embeddings.embed_query(question)
        embedded_questions.append(embedding)

    end_time = time.time()
    total_time = end_time - start_time
    avg_time_per_doc = total_time / len(questions)

    print(f"\n✅ Sequential Embedding Complete!")
    print(f"   Total time: {total_time:.2f} seconds")
    print(f"   Average time per document: {avg_time_per_doc*1000:.2f} ms")
    print(f"   Throughput: {len(questions)/total_time:.2f} docs/sec")

    return {
        "method": "sequential",
        "total_time": total_time,
        "avg_time_per_doc": avg_time_per_doc,
        "throughput": len(questions)/total_time,
        "num_docs": len(questions)
    }


def benchmark_batch_embedding(questions: list[str], embeddings: OpenAIEmbeddings, batch_size: int = 100) -> dict:
    """Benchmark batch embedding (new approach)."""
    print("\n" + "="*80)
    print("BENCHMARK 2: Batch Embedding (NEW)")
    print("="*80)
    print(f"Processing {len(questions)} questions in batches of {batch_size}...")

    start_time = time.time()
    embedded_questions = []

    for i in range(0, len(questions), batch_size):
        batch = questions[i:i+batch_size]
        print(f"Progress: {i}/{len(questions)} ({i*100//len(questions)}%) - Processing batch of {len(batch)} documents...", flush=True)

        batch_embeddings = embeddings.embed_documents(batch)
        embedded_questions.extend(batch_embeddings)

    end_time = time.time()
    total_time = end_time - start_time
    avg_time_per_doc = total_time / len(questions)

    print(f"\n✅ Batch Embedding Complete!")
    print(f"   Total time: {total_time:.2f} seconds")
    print(f"   Average time per document: {avg_time_per_doc*1000:.2f} ms")
    print(f"   Throughput: {len(questions)/total_time:.2f} docs/sec")

    return {
        "method": "batch",
        "total_time": total_time,
        "avg_time_per_doc": avg_time_per_doc,
        "throughput": len(questions)/total_time,
        "num_docs": len(questions),
        "batch_size": batch_size
    }


def print_comparison(sequential_result: dict, batch_result: dict):
    """Print performance comparison."""
    print("\n" + "="*80)
    print("PERFORMANCE COMPARISON")
    print("="*80)

    speedup = sequential_result["total_time"] / batch_result["total_time"]
    time_saved = sequential_result["total_time"] - batch_result["total_time"]
    time_saved_pct = (time_saved / sequential_result["total_time"]) * 100

    print(f"\n📊 Dataset: {sequential_result['num_docs']} documents")
    print(f"\n⏱️  Total Time:")
    print(f"   Sequential: {sequential_result['total_time']:.2f}s")
    print(f"   Batch:      {batch_result['total_time']:.2f}s")
    print(f"   ⚡ Speedup:  {speedup:.2f}x faster")
    print(f"   ⏰ Saved:    {time_saved:.2f}s ({time_saved_pct:.1f}% reduction)")

    print(f"\n📈 Throughput:")
    print(f"   Sequential: {sequential_result['throughput']:.2f} docs/sec")
    print(f"   Batch:      {batch_result['throughput']:.2f} docs/sec")

    print(f"\n💡 Per Document:")
    print(f"   Sequential: {sequential_result['avg_time_per_doc']*1000:.2f} ms/doc")
    print(f"   Batch:      {batch_result['avg_time_per_doc']*1000:.2f} ms/doc")

    # Extrapolate to larger datasets
    print(f"\n🔮 Extrapolation for Larger Datasets:")
    for size in [500, 1000, 5000]:
        seq_time = sequential_result['avg_time_per_doc'] * size
        batch_time = batch_result['avg_time_per_doc'] * size
        print(f"   {size} docs:")
        print(f"      Sequential: {seq_time/60:.1f} min  |  Batch: {batch_time/60:.1f} min  |  Saved: {(seq_time-batch_time)/60:.1f} min")

    print("\n" + "="*80)


def main():
    """Main benchmark execution."""
    import os
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    print("\n🚀 EMBEDDING PERFORMANCE BENCHMARK")
    print("="*80)
    print("Testing: generated_user_manual.xlsx")
    print("Comparing: Sequential vs Batch Embedding API")

    # Load data
    file_path = "generated_user_manual.xlsx"
    print(f"\nLoading data from {file_path}...")
    questions = load_qa_data(file_path)
    print(f"✅ Loaded {len(questions)} questions")

    # Initialize embeddings
    print("\nInitializing OpenAI Embeddings...")
    embeddings = OpenAIEmbeddings()
    print("✅ Embeddings initialized")

    # Run benchmarks
    sequential_result = benchmark_sequential_embedding(questions, embeddings)
    batch_result = benchmark_batch_embedding(questions, embeddings, batch_size=100)

    # Print comparison
    print_comparison(sequential_result, batch_result)

    # Save results
    results_summary = f"""
# Embedding Performance Benchmark Results

**Dataset**: {file_path}
**Total Documents**: {sequential_result['num_docs']}
**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}

## Results

| Metric | Sequential (OLD) | Batch (NEW) | Improvement |
|--------|------------------|-------------|-------------|
| Total Time | {sequential_result['total_time']:.2f}s | {batch_result['total_time']:.2f}s | {sequential_result['total_time']/batch_result['total_time']:.2f}x faster |
| Throughput | {sequential_result['throughput']:.2f} docs/sec | {batch_result['throughput']:.2f} docs/sec | {batch_result['throughput']/sequential_result['throughput']:.2f}x |
| Time per Doc | {sequential_result['avg_time_per_doc']*1000:.2f}ms | {batch_result['avg_time_per_doc']*1000:.2f}ms | {(1 - batch_result['avg_time_per_doc']/sequential_result['avg_time_per_doc'])*100:.1f}% faster |

## Key Findings

1. **Batch embedding is {sequential_result['total_time']/batch_result['total_time']:.2f}x faster** than sequential embedding
2. **Time saved**: {sequential_result['total_time'] - batch_result['total_time']:.2f}s ({(sequential_result['total_time'] - batch_result['total_time'])/sequential_result['total_time']*100:.1f}% reduction)
3. **Batch size**: {batch_result['batch_size']} documents per API call

## Extrapolation

For larger datasets, the performance gain becomes even more significant:

- **500 documents**: Sequential ~{sequential_result['avg_time_per_doc']*500/60:.1f}min vs Batch ~{batch_result['avg_time_per_doc']*500/60:.1f}min (save {(sequential_result['avg_time_per_doc']*500 - batch_result['avg_time_per_doc']*500)/60:.1f}min)
- **1000 documents**: Sequential ~{sequential_result['avg_time_per_doc']*1000/60:.1f}min vs Batch ~{batch_result['avg_time_per_doc']*1000/60:.1f}min (save {(sequential_result['avg_time_per_doc']*1000 - batch_result['avg_time_per_doc']*1000)/60:.1f}min)
- **5000 documents**: Sequential ~{sequential_result['avg_time_per_doc']*5000/60:.1f}min vs Batch ~{batch_result['avg_time_per_doc']*5000/60:.1f}min (save {(sequential_result['avg_time_per_doc']*5000 - batch_result['avg_time_per_doc']*5000)/60:.1f}min)
"""

    with open("benchmark_results.md", "w") as f:
        f.write(results_summary)

    print("\n✅ Results saved to benchmark_results.md")
    print("\n🎉 Benchmark complete!")


if __name__ == "__main__":
    main()
