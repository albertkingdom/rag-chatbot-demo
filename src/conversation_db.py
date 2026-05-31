import asyncio
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from pymongo import MongoClient
import uuid

class ConversationDatabase:
    def __init__(self, mongodb_url: str = None):
        if not mongodb_url:
            mongodb_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017/chatbot")
        
        self.client = MongoClient(mongodb_url)
        # Extract database name from URL if present, otherwise default to 'chatbot'
        db_name = mongodb_url.split("/")[-1] or "chatbot"
        if "?" in db_name:
            db_name = db_name.split("?")[0]
            
        self.db = self.client[db_name]
        self.collection = self.db["conversations"]
        
        # Ensure indexes for faster querying
        self.collection.create_index("session_id")
        self.collection.create_index("timestamp")

    def save_conversation(
        self,
        user_question: str,
        assistant_response: str,
        session_id: str = None,
        response_source: str = "rag",
        intent_classification: Dict[str, Any] = None,
        cache_hit: bool = False,
        cache_similarity: float = 0.0,
        metadata: Dict[str, Any] = None
    ) -> str:
        """
        Saves a conversation entry to MongoDB.
        """
        document = {
            "session_id": session_id or str(uuid.uuid4()),
            "user_question": user_question,
            "assistant_response": assistant_response,
            "response_source": response_source,
            "intent_classification": intent_classification or {},
            "cache_hit": cache_hit,
            "cache_similarity": cache_similarity,
            "timestamp": datetime.utcnow(),
            "metadata": metadata or {}
        }
        
        result = self.collection.insert_one(document)
        return str(result.inserted_id)

    async def async_save_conversation(self, **kwargs) -> str:
        return await asyncio.to_thread(self.save_conversation, **kwargs)

    def get_recent_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves the most recent conversations.
        """
        cursor = self.collection.find().sort("timestamp", -1).limit(limit)
        return list(cursor)

    def get_stats(self) -> Dict[str, Any]:
        """
        Calculates basic statistics about the conversations.
        """
        total_count = self.collection.count_documents({})
        if total_count == 0:
            return {
                "total_conversations": 0,
                "cache_hit_rate": 0,
                "relevant_rate": 0
            }
            
        cache_hits = self.collection.count_documents({"cache_hit": True})
        relevant_count = self.collection.count_documents({"intent_classification.relevant": True})
        
        return {
            "total_conversations": total_count,
            "cache_hit_rate": (cache_hits / total_count) * 100,
            "relevant_rate": (relevant_count / total_count) * 100
        }

# Singleton instance
_db_instance = None

def get_conversation_db():
    global _db_instance
    if _db_instance is None:
        _db_instance = ConversationDatabase()
    return _db_instance
