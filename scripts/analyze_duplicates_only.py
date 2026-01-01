#!/usr/bin/env python3
"""
Script to analyze duplicate vectors in Pinecone (read-only).
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone
from collections import defaultdict
from src.config import PINECONE_INDEX_NAME


def main():
    """Main execution."""
    load_dotenv()

    print("\n🔍 PINECONE DUPLICATE ANALYSIS")
    print("="*80)

    # Connect to Pinecone
    print("\n🔌 Connecting to Pinecone...")
    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("❌ Error: PINECONE_API_KEY not found!")
        return

    pc = Pinecone(api_key=api_key)
    index = pc.Index(PINECONE_INDEX_NAME)
    print(f"✅ Connected to index: {PINECONE_INDEX_NAME}")

    # Fetch all vector IDs
    print("\n📦 Fetching all vector IDs...")
    all_vector_ids = []
    for ids_batch in index.list():
        all_vector_ids.extend(ids_batch)

    print(f"✅ Found {len(all_vector_ids)} total vectors")

    # Group by hash
    print("\n🔍 Grouping vectors by question hash...")
    hash_groups = defaultdict(list)

    for vec_id in all_vector_ids:
        parts = vec_id.split('_')
        if len(parts) >= 3 and parts[0] == 'qa':
            question_hash = parts[1]
            hash_groups[question_hash].append(vec_id)

    # Find duplicates
    duplicates = {k: v for k, v in hash_groups.items() if len(v) > 1}
    unique = {k: v for k, v in hash_groups.items() if len(v) == 1}

    print(f"\n" + "="*80)
    print("📊 ANALYSIS RESULTS")
    print("="*80)
    print(f"\n總向量數: {len(all_vector_ids)}")
    print(f"唯一問題數 (by hash): {len(hash_groups)}")
    print(f"無重複的問題: {len(unique)}")
    print(f"有重複的問題: {len(duplicates)}")
    print(f"重複向量總數: {sum(len(v) - 1 for v in duplicates.values())}")

    if duplicates:
        print(f"\n" + "="*80)
        print("📋 重複詳情 (前 15 個)")
        print("="*80)

        for i, (hash_val, vec_ids) in enumerate(list(duplicates.items())[:15], 1):
            print(f"\n{i}. Hash: {hash_val}")
            print(f"   重複次數: {len(vec_ids)} 個向量")
            print(f"   Vector IDs: {', '.join(vec_ids)}")

            # Fetch to show actual question
            try:
                fetch_response = index.fetch(ids=[vec_ids[0]])
                if fetch_response.vectors:
                    vec_data = list(fetch_response.vectors.values())[0]
                    question = vec_data.metadata.get('text', 'N/A')
                    print(f"   問題: {question}")
            except Exception as e:
                print(f"   (無法獲取問題文本: {e})")

        print(f"\n💡 建議:")
        print(f"   - 可以移除 {sum(len(v) - 1 for v in duplicates.values())} 個重複向量")
        print(f"   - 移除後將剩下 {len(hash_groups)} 個唯一問題")
        print(f"   - 使用 detect_and_remove_duplicates.py 來執行去重")

    else:
        print(f"\n🎉 沒有發現重複項目！")

    print("\n" + "="*80)


if __name__ == "__main__":
    main()
