import asyncio
import os
from dotenv import load_dotenv

async def run_test():
    """Loads environment, imports the RAG chain, and tests the astream method."""
    
    # Load environment variables from .env file
    load_dotenv()
    
    print("--- Loading RAG Chain from app.py ---")
    try:
        # It's important to import AFTER loading dotenv
        from app import rag_chain
        print("RAG Chain loaded successfully.")
    except Exception as e:
        print(f"Error loading RAG chain: {e}")
        return

    test_message = "What is the purpose of this system?"
    print(f"\n--- Testing astream with message: '{test_message}' ---")

    try:
        full_response = ""
        async for chunk in rag_chain.astream(test_message):
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
