#!/usr/bin/env python3
"""
Interactive script to understand embeddings and dimensionality.

This script helps visualize and understand what 1536-dimensional embeddings mean.
"""

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
import json


def explain_dimensions():
    """Explain dimensions with examples."""
    print("\n" + "="*80)
    print("📐 UNDERSTANDING DIMENSIONS")
    print("="*80)

    print("\n🔢 從低維度到高維度的旅程：")
    print("\n" + "-"*80)

    print("\n1️⃣  一維空間 (1D) - 一條線")
    print("   想像：溫度計、數字線")
    print("   例子：溫度 = [25.5]")
    print("   用途：只能表示一個特徵（如：溫度高低）")
    print("   ━━━━━━━━━━━━━━━━━")
    print("   0    25.5    100 (溫度)")

    print("\n2️⃣  二維空間 (2D) - 一個平面")
    print("   想像：地圖、座標系")
    print("   例子：位置 = [經度, 緯度]")
    print("   用途：可以表示兩個特徵（如：位置）")
    print("   ")
    print("   緯度 ↑")
    print("       |  • (121.5, 25.0)")
    print("       |")
    print("       └──────→ 經度")

    print("\n3️⃣  三維空間 (3D) - 立體空間")
    print("   想像：我們生活的物理世界")
    print("   例子：顏色 = [紅, 綠, 藍] 或 空間位置 = [x, y, z]")
    print("   用途：可以表示三個特徵")
    print("   ")
    print("        z (高度)")
    print("        ↑")
    print("        |   • (x, y, z)")
    print("        |  /")
    print("        | /")
    print("        └────→ y (寬度)")
    print("       /")
    print("      / x (深度)")

    print("\n4️⃣  更高維度 (4D, 5D, ..., 1536D)")
    print("   想像：無法直接視覺化，但可以用數學理解")
    print("   例子：")
    print("   • 10D：描述一個人的特徵 [身高, 體重, 年齡, 收入, 教育, ...]")
    print("   • 100D：描述一張圖片的特徵")
    print("   • 1536D：描述一段文字的「語義」（意思）")

    print("\n💡 關鍵概念：")
    print("   - 每增加一個維度，就多一個「特徵」或「方向」")
    print("   - 1536 維 = 用 1536 個數字來描述一件事情")
    print("   - 這些數字共同捕捉了文字的「意義」")


def demonstrate_word_embeddings():
    """Show how words are represented in high-dimensional space."""
    print("\n" + "="*80)
    print("🔤 文字 → 向量的轉換")
    print("="*80)

    load_dotenv()
    embeddings = OpenAIEmbeddings()

    # Example words with semantic relationships
    words = [
        "國王",
        "皇后",
        "男人",
        "女人",
        "蘋果",
        "橘子"
    ]

    print("\n正在生成 embeddings...")
    word_embeddings = {}
    for word in words:
        embedding = embeddings.embed_query(word)
        word_embeddings[word] = embedding
        print(f"✓ {word}")

    print("\n" + "-"*80)
    print("📊 Embedding 結果展示：")
    print("-"*80)

    for word, embedding in list(word_embeddings.items())[:3]:
        print(f"\n「{word}」的 embedding (前 10 個維度):")
        print(f"   {embedding[:10]}")
        print(f"   ... (還有 {len(embedding) - 10} 個維度)")
        print(f"   總共：{len(embedding)} 個數字")


def calculate_similarity():
    """Calculate and explain similarity between embeddings."""
    print("\n" + "="*80)
    print("📏 相似度計算 - 高維空間中的「距離」")
    print("="*80)

    load_dotenv()
    embeddings = OpenAIEmbeddings()

    # Pairs with different semantic relationships
    pairs = [
        ("如何登入系統？", "怎麼登入帳號？"),  # 非常相似
        ("如何登入系統？", "如何設定密碼？"),  # 中等相似
        ("如何登入系統？", "今天天氣如何？"),  # 不相似
    ]

    print("\n計算三組問題的相似度...")

    results = []
    for q1, q2 in pairs:
        emb1 = embeddings.embed_query(q1)
        emb2 = embeddings.embed_query(q2)

        # Calculate cosine similarity
        dot_product = np.dot(emb1, emb2)
        norm1 = np.linalg.norm(emb1)
        norm2 = np.linalg.norm(emb2)
        similarity = dot_product / (norm1 * norm2)

        results.append({
            "問題 1": q1,
            "問題 2": q2,
            "相似度": f"{similarity:.4f}"
        })

    print("\n" + "-"*80)
    print("結果分析：")
    print("-"*80)

    for i, result in enumerate(results, 1):
        print(f"\n{i}. 問題對比：")
        print(f"   問題 1: 「{result['問題 1']}」")
        print(f"   問題 2: 「{result['問題 2']}」")
        print(f"   相似度: {result['相似度']} (範圍: -1 到 1，越接近 1 越相似)")

        similarity_val = float(result['相似度'])
        if similarity_val > 0.9:
            print(f"   解讀: 🟢 非常相似 - 幾乎是同一個意思")
        elif similarity_val > 0.7:
            print(f"   解讀: 🟡 中等相似 - 有關聯但不完全相同")
        else:
            print(f"   解讀: 🔴 不相似 - 完全不同的主題")

    print("\n💡 這就是為什麼需要 1536 維度：")
    print("   - 捕捉複雜的語義關係")
    print("   - 「登入」和「登錄」語義相近 → 在高維空間中距離很近")
    print("   - 「登入」和「天氣」語義不同 → 在高維空間中距離很遠")


def visualize_dimension_reduction():
    """Explain how high dimensions are used."""
    print("\n" + "="*80)
    print("🎨 為什麼需要這麼多維度？")
    print("="*80)

    print("\n📚 類比：用數字描述一本書")
    print("-"*80)

    print("\n如果只用 3 個維度描述一本書：")
    print("   [頁數, 價格, 年份]")
    print("   → 完全無法知道書的「內容」！")
    print("   → 《哈利波特》和《資本論》可能看起來很像（都很厚、都很貴）")

    print("\n如果用 10 個維度：")
    print("   [頁數, 價格, 年份, 是否虛構, 是否暢銷, 閱讀難度, ...]")
    print("   → 稍微好一點，但還是不夠")

    print("\n如果用 1536 個維度：")
    print("   → 每個維度捕捉文字的某個「抽象特徵」")
    print("   → 維度 1: 可能代表「科技相關程度」")
    print("   → 維度 2: 可能代表「情感正負面」")
    print("   → 維度 3: 可能代表「正式/非正式語氣」")
    print("   → ... (1533 個其他特徵)")
    print("   → 這些維度由 AI 自動學習，不是人為設定的！")

    print("\n🎯 實際應用：")
    print("-"*80)
    print("\n在您的 RAG 系統中：")
    print("   1. 用戶問：「如何重設密碼？」")
    print("      → 轉換成 1536 個數字")
    print("   ")
    print("   2. 在向量資料庫中搜尋")
    print("      → 找到最接近的 1536 維向量")
    print("   ")
    print("   3. 找到相似的問題：")
    print("      ✓ \"我該如何首次重設我的密碼？\" (相似度: 0.92)")
    print("      ✓ \"忘記密碼怎麼辦？\" (相似度: 0.87)")
    print("      ✗ \"如何登出帳號？\" (相似度: 0.45)")


def show_actual_embedding():
    """Show an actual embedding vector."""
    print("\n" + "="*80)
    print("🔍 實際的 Embedding 長什麼樣子？")
    print("="*80)

    load_dotenv()
    embeddings = OpenAIEmbeddings()

    question = "如何登入系統？"
    print(f"\n問題: 「{question}」")
    print("\n轉換為 1536 維向量...\n")

    embedding = embeddings.embed_query(question)

    print("完整的 Embedding 向量：")
    print("-"*80)

    # Show in chunks
    chunk_size = 20
    for i in range(0, min(100, len(embedding)), chunk_size):
        chunk = embedding[i:i+chunk_size]
        print(f"維度 {i:4d}-{i+chunk_size-1:4d}: {[f'{x:.4f}' for x in chunk]}")

    print(f"\n... (省略中間部分)")

    # Show last chunk
    last_chunk_start = len(embedding) - chunk_size
    last_chunk = embedding[last_chunk_start:]
    print(f"維度 {last_chunk_start:4d}-{len(embedding)-1:4d}: {[f'{x:.4f}' for x in last_chunk]}")

    print(f"\n📊 統計資訊：")
    print(f"   總維度數: {len(embedding)}")
    print(f"   最小值: {min(embedding):.6f}")
    print(f"   最大值: {max(embedding):.6f}")
    print(f"   平均值: {np.mean(embedding):.6f}")
    print(f"   標準差: {np.std(embedding):.6f}")

    print("\n💡 每個數字的意義：")
    print("   - 大多數值在 -0.05 到 0.05 之間")
    print("   - 正值和負值都有意義")
    print("   - 這 1536 個數字共同定義了這個問題在「語義空間」中的位置")
    print("   - AI 模型學會了如何將相似意思的句子映射到相近的向量")


def main():
    """Main execution."""
    print("\n" + "="*80)
    print("🧠 理解 1536 維度的 Embeddings")
    print("="*80)

    # Step 1: Explain dimensions conceptually
    explain_dimensions()

    # Step 2: Show actual embedding
    show_actual_embedding()

    # Step 3: Demonstrate word embeddings
    demonstrate_word_embeddings()

    # Step 4: Calculate similarity
    calculate_similarity()

    # Step 5: Explain why high dimensions
    visualize_dimension_reduction()

    print("\n" + "="*80)
    print("🎓 總結")
    print("="*80)
    print("\n1536 維度的本質：")
    print("   ✓ 用 1536 個數字來描述一段文字的「意思」")
    print("   ✓ 每個維度捕捉語義的某個抽象方面")
    print("   ✓ 相似意思的文字 → 在高維空間中距離近")
    print("   ✓ 不同意思的文字 → 在高維空間中距離遠")
    print("\n為什麼需要這麼多維度：")
    print("   ✓ 語言很複雜：語氣、情感、主題、專業性...")
    print("   ✓ 維度越多 → 能捕捉的細節越豐富")
    print("   ✓ 1536 是 OpenAI 模型經過優化的平衡點")
    print("\n在 RAG 系統中的作用：")
    print("   ✓ 將問題和答案轉換為數學可計算的形式")
    print("   ✓ 在高維空間中快速找到最相關的內容")
    print("   ✓ 實現「語義搜尋」而非「關鍵字搜尋」")
    print("\n" + "="*80)


if __name__ == "__main__":
    main()
