#!/usr/bin/env python3
"""
Script to detect and remove duplicate vectors in Pinecone.

Duplicates are identified by:
1. Same question hash (same question text)
2. Same question text in metadata
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone
from collections import defaultdict
from src.config import PINECONE_INDEX_NAME


def analyze_duplicates(index):
    """Analyze and identify duplicate vectors."""
    print("\n" + "="*80)
    print("🔍 ANALYZING DUPLICATES IN PINECONE")
    print("="*80)

    print("\n📦 Fetching all vector IDs...")
    all_vector_ids = []
    for ids_batch in index.list():
        all_vector_ids.extend(ids_batch)

    print(f"✅ Found {len(all_vector_ids)} total vectors")

    # Group by hash (the middle part of ID: qa_<hash>_<index>)
    print("\n🔍 Grouping vectors by question hash...")
    hash_groups = defaultdict(list)

    for vec_id in all_vector_ids:
        parts = vec_id.split('_')
        if len(parts) >= 3 and parts[0] == 'qa':
            question_hash = parts[1]  # Extract hash
            hash_groups[question_hash].append(vec_id)

    # Find duplicates (hashes with multiple vectors)
    duplicates = {k: v for k, v in hash_groups.items() if len(v) > 1}
    unique = {k: v for k, v in hash_groups.items() if len(v) == 1}

    print(f"\n📊 Analysis Results:")
    print(f"   Total unique questions (by hash): {len(hash_groups)}")
    print(f"   Questions with NO duplicates: {len(unique)}")
    print(f"   Questions WITH duplicates: {len(duplicates)}")
    print(f"   Total duplicate vectors: {sum(len(v) - 1 for v in duplicates.values())}")

    return duplicates, unique, all_vector_ids


def display_duplicate_details(index, duplicates, limit=10):
    """Display detailed information about duplicates."""
    print("\n" + "="*80)
    print(f"📋 DUPLICATE DETAILS (showing first {limit})")
    print("="*80)

    for i, (hash_val, vec_ids) in enumerate(list(duplicates.items())[:limit], 1):
        print(f"\n{i}. Question Hash: {hash_val}")
        print(f"   Duplicate count: {len(vec_ids)} vectors")
        print(f"   Vector IDs:")

        # Fetch these vectors to see their metadata
        fetch_response = index.fetch(ids=vec_ids[:5])  # Limit to 5 per group
        vectors = fetch_response.vectors

        for vec_id, vec_data in vectors.items():
            question = vec_data.metadata.get('text', 'N/A')[:80]
            answer = vec_data.metadata.get('answer', 'N/A')[:80]
            print(f"      - ID: {vec_id}")
            print(f"        Q: {question}...")
            print(f"        A: {answer}...")


def confirm_deduplication():
    """Ask user for confirmation."""
    print("\n" + "="*80)
    print("⚠️  DEDUPLICATION STRATEGY")
    print("="*80)

    print("\n📝 How to handle duplicates:")
    print("   For each group of duplicate vectors (same question):")
    print("   → Keep: The FIRST vector (lowest index number)")
    print("   → Delete: All other duplicate vectors")
    print("\n   Example:")
    print("   • qa_1234_5  ← Keep (lowest index)")
    print("   • qa_1234_18 ← Delete")
    print("   • qa_1234_45 ← Delete")

    response = input("\n🤔 Proceed with deduplication? (yes/no): ").strip().lower()
    return response in ['yes', 'y']


def remove_duplicates(index, duplicates):
    """Remove duplicate vectors, keeping only the first one."""
    print("\n" + "="*80)
    print("🗑️  REMOVING DUPLICATES")
    print("="*80)

    total_to_delete = 0
    ids_to_delete = []

    for hash_val, vec_ids in duplicates.items():
        # Sort by index (the last part of ID)
        sorted_ids = sorted(vec_ids, key=lambda x: int(x.split('_')[-1]))

        # Keep the first, delete the rest
        to_delete = sorted_ids[1:]
        ids_to_delete.extend(to_delete)
        total_to_delete += len(to_delete)

    print(f"\n📊 Deduplication Summary:")
    print(f"   Duplicate groups: {len(duplicates)}")
    print(f"   Vectors to delete: {total_to_delete}")
    print(f"   Vectors to keep: {len(duplicates)}")

    if not ids_to_delete:
        print("\n✅ No duplicates to remove!")
        return

    # Delete in batches
    batch_size = 100
    print(f"\n🗑️  Deleting vectors in batches of {batch_size}...")

    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i+batch_size]
        print(f"   Deleting batch {i//batch_size + 1}/{(len(ids_to_delete)-1)//batch_size + 1} ({len(batch)} vectors)...")
        index.delete(ids=batch)

    print(f"\n✅ Successfully deleted {total_to_delete} duplicate vectors!")


def verify_deduplication(index):
    """Verify that deduplication was successful."""
    print("\n" + "="*80)
    print("✅ VERIFYING DEDUPLICATION")
    print("="*80)

    print("\n📦 Fetching updated vector IDs...")
    all_vector_ids = []
    for ids_batch in index.list():
        all_vector_ids.extend(ids_batch)

    print(f"✅ Current total vectors: {len(all_vector_ids)}")

    # Check for remaining duplicates
    hash_groups = defaultdict(list)
    for vec_id in all_vector_ids:
        parts = vec_id.split('_')
        if len(parts) >= 3 and parts[0] == 'qa':
            question_hash = parts[1]
            hash_groups[question_hash].append(vec_id)

    duplicates = {k: v for k, v in hash_groups.items() if len(v) > 1}

    if duplicates:
        print(f"\n⚠️  Warning: Still found {len(duplicates)} duplicate groups!")
        for hash_val, vec_ids in list(duplicates.items())[:5]:
            print(f"   Hash {hash_val}: {len(vec_ids)} vectors")
    else:
        print(f"\n🎉 Success! No duplicates found!")
        print(f"   Total unique questions: {len(hash_groups)}")


def update_sync_function_to_prevent_duplicates():
    """Provide instructions to prevent future duplicates."""
    print("\n" + "="*80)
    print("🔧 PREVENTING FUTURE DUPLICATES")
    print("="*80)

    print("\n📝 To prevent duplicates in future syncs, we should update:")
    print("   File: src/build_vector_store.py")
    print("\n   Current ID generation:")
    print("   ```python")
    print("   doc_id = f\"qa_{abs(hash(question))}_{i}\"")
    print("   ```")
    print("\n   ❌ Problem: Same question gets different IDs (due to different i)")
    print("\n   ✅ Solution: Use only the hash (remove the index i)")
    print("   ```python")
    print("   doc_id = f\"qa_{abs(hash(question))}\"")
    print("   ```")
    print("\n   Or use a deduplication dict:")
    print("   ```python")
    print("   seen_questions = {}  # question text -> doc_id")
    print("   for i, doc_data in enumerate(all_docs_data):")
    print("       question = doc_data['question']")
    print("       if question in seen_questions:")
    print("           continue  # Skip duplicate")
    print("       doc_id = f\"qa_{abs(hash(question))}\"")
    print("       seen_questions[question] = doc_id")
    print("   ```")


def main():
    """Main execution."""
    load_dotenv()

    print("\n🔍 PINECONE DEDUPLICATION TOOL")
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

    # Step 1: Analyze duplicates
    duplicates, unique, all_vector_ids = analyze_duplicates(index)

    if not duplicates:
        print("\n🎉 No duplicates found! Your index is clean.")
        return

    # Step 2: Display duplicate details
    display_duplicate_details(index, duplicates, limit=10)

    # Step 3: Confirm deduplication
    if not confirm_deduplication():
        print("\n❌ Deduplication cancelled by user.")
        return

    # Step 4: Remove duplicates
    remove_duplicates(index, duplicates)

    # Step 5: Verify
    verify_deduplication(index)

    # Step 6: Show how to prevent future duplicates
    update_sync_function_to_prevent_duplicates()

    print("\n" + "="*80)
    print("🎉 DEDUPLICATION COMPLETE!")
    print("="*80)


if __name__ == "__main__":
    main()
