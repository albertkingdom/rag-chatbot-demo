"""Manual smoke script for the RAG generation stream.

Not a pytest test (no test_* functions) — run it directly to exercise the
generation chain against real services. It imports from src.rag_pipeline
(get_generation_chain) instead of the long-removed `from app import rag_chain`.

Usage:
    venv/bin/python tests/test_rag_stream.py
"""
import asyncio
import os
from dotenv import load_dotenv


async def run_test():
    """Loads environment, imports the generation chain, and tests astream."""
    load_dotenv()

    print("--- Loading generation chain from src.rag_pipeline ---")
    try:
        from src.rag_pipeline import get_generation_chain
        print("Generation chain loaded successfully.")
    except Exception as e:
        print(f"Error loading generation chain: {e}")
        return

    test_message = "What is the purpose of this system?"
    print(f"\n--- Testing astream with message: '{test_message}' ---")

    try:
        full_response = ""
        chain = get_generation_chain()
        async for chunk in chain.astream(
            {"context": "", "question": test_message, "history": ""}
        ):
            print(chunk, end="", flush=True)
            full_response += chunk

        print("\n\n--- Stream finished ---")
        if not full_response:
            print("Warning: Stream produced no output.")

    except Exception as e:
        print("\n\n--- An error occurred during streaming ---")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_test())
