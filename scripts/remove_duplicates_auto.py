#!/usr/bin/env python3
"""
Automatically remove duplicate vectors from Pinecone.
Keeps the first occurrence (lowest index) of each duplicate.
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone
from collections import defaultdict
from src.config import PINECONE_INDEX_NAME


def main():
    """Main execution."""
    load_dotenv()

    print("\n🗑️  PINECONE AUTOMATIC DEDUPLICATION")
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
    print("\n🔍 Analyzing duplicates...")
    hash_groups = defaultdict(list)

    for vec_id in all_vector_ids:
        parts = vec_id.split('_')
        if len(parts) >= 3 and parts[0] == 'qa':
            question_hash = parts[1]
            hash_groups[question_hash].append(vec_id)

    # Find duplicates
    duplicates = {k: v for k, v in hash_groups.items() if len(v) > 1}

    if not duplicates:
        print("\n🎉 No duplicates found!")
        return

    # Prepare deletion list
    print(f"\n📊 Deduplication Plan:")
    print(f"   Unique questions: {len(hash_groups)}")
    print(f"   Duplicate groups: {len(duplicates)}")

    ids_to_delete = []
    for hash_val, vec_ids in duplicates.items():
        # Sort by index (last part of ID)
        sorted_ids = sorted(vec_ids, key=lambda x: int(x.split('_')[-1]))

        # Keep first, delete rest
        to_delete = sorted_ids[1:]
        ids_to_delete.extend(to_delete)

    print(f"   Vectors to delete: {len(ids_to_delete)}")
    print(f"   Vectors to keep: {len(hash_groups)}")

    # Delete in batches
    print(f"\n🗑️  Deleting duplicate vectors...")
    batch_size = 100

    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i+batch_size]
        batch_num = i//batch_size + 1
        total_batches = (len(ids_to_delete)-1)//batch_size + 1

        print(f"   Batch {batch_num}/{total_batches}: Deleting {len(batch)} vectors...")
        index.delete(ids=batch)

    print(f"\n✅ Successfully deleted {len(ids_to_delete)} duplicate vectors!")

    # Verify
    print(f"\n🔍 Verifying deduplication...")
    all_vector_ids_after = []
    for ids_batch in index.list():
        all_vector_ids_after.extend(ids_batch)

    print(f"   Before: {len(all_vector_ids)} vectors")
    print(f"   After:  {len(all_vector_ids_after)} vectors")
    print(f"   Deleted: {len(all_vector_ids) - len(all_vector_ids_after)} vectors")

    # Check for remaining duplicates
    hash_groups_after = defaultdict(list)
    for vec_id in all_vector_ids_after:
        parts = vec_id.split('_')
        if len(parts) >= 3 and parts[0] == 'qa':
            question_hash = parts[1]
            hash_groups_after[question_hash].append(vec_id)

    duplicates_after = {k: v for k, v in hash_groups_after.items() if len(v) > 1}

    if duplicates_after:
        print(f"\n⚠️  Warning: Still found {len(duplicates_after)} duplicate groups!")
    else:
        print(f"\n🎉 Success! No duplicates remaining!")

    print("\n" + "="*80)
    print("✅ DEDUPLICATION COMPLETE!")
    print("="*80)


if __name__ == "__main__":
    main()
