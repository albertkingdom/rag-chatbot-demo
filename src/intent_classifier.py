"""
Intent Classifier for Carbon Management System.
Filters out off-topic questions before entering the RAG pipeline.
"""
import os
from typing import Dict, Any
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langsmith import traceable


class IntentResult(BaseModel):
    relevant: bool = Field(description="Whether the question is related to the carbon management system")
    confidence: float = Field(description="Confidence score from 0.0 to 1.0")
    reason: str = Field(description="Brief explanation of the classification")


def _format_recent_turns(history: list, max_turns: int = 3) -> str:
    """Builds a short "最近對話：" block from the last few user/assistant turns.

    Mirrors the turn-pairing shape of rag_pipeline.format_history but is
    implemented locally: rag_pipeline imports services.get_intent_classifier,
    which imports this module, so importing rag_pipeline here would create a
    circular import.
    """
    if not history:
        return ""

    turns = []
    i = 0
    while i < len(history) - 1:
        msg = history[i]
        next_msg = history[i + 1]
        if isinstance(msg, dict) and isinstance(next_msg, dict):
            if msg.get("role") == "user" and next_msg.get("role") == "assistant":
                turns.append((msg["content"], next_msg["content"]))
                i += 2
                continue
        i += 1

    recent = turns[-max_turns:] if turns else []
    if not recent:
        return ""

    lines = ["最近對話："]
    for user_msg, bot_msg in recent:
        lines.append(f"User: {user_msg}")
        lines.append(f"Assistant: {bot_msg}")
    return "\n".join(lines)


class IntentClassifier:
    """
    Classifies user questions to determine if they are related to the carbon management system.

    This classifier acts as a gatekeeper before the RAG pipeline, saving costs and improving
    user experience by rejecting off-topic questions early.
    """

    # System-relevant topics based on the actual QA dataset
    SYSTEM_TOPICS = """
碳管理系統 (CarbonM) 相關主題：
1. 帳號管理：登入、登出、密碼重設、權限設定、使用者管理
2. 盤查操作：活動數據蒐集、類別 4.1-4.6 排放、數據輸入
3. 廠區管理：邊界設定、盤查邊界、多廠區切換、場址管理
4. 排放源知識：固定源、移動源、移動式燃料源、生質能源、汽電共生設備
5. 環境法規：溫室氣體盤查、環境部登錄、許可證、服務條款
6. 數據計算：排放係數、碳足跡、碳排放計算、活動數據
7. 系統功能：報表匯出、數據查詢、狀態檢視

無關主題範例：
- 天氣、美食、旅遊、娛樂、購物
- 通用知識問答（歷史、地理、數學、科學）
- 創作需求（寫詩、寫故事、寫程式）
- 其他公司或產品（非 CarbonM）
- 閒聊或問候語（除非緊接著詢問系統問題）
"""

    def __init__(self, openrouter_api_key: str = None):
        api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY must be provided or set in environment")

        llm = ChatOpenAI(
            model="google/gemini-2.5-flash",
            temperature=0,
            openai_api_key=api_key,
            openai_api_base="https://openrouter.ai/api/v1",
        )
        self.structured_llm = llm.with_structured_output(IntentResult)

    @traceable(name="Intent Classification")
    async def classify(self, question: str, history: list = None) -> Dict[str, Any]:
        history_block = _format_recent_turns(history)
        history_section = f"\n{history_block}\n" if history_block else ""

        prompt = f"""{self.SYSTEM_TOPICS}

你是碳管理系統的問題分類器。判斷以下問題是否與碳管理系統相關。
{history_section}
分類範例：
- 「今天天氣如何？」→ relevant=false, confidence=0.95, reason="詢問天氣資訊"
- 「如何重設密碼？」→ relevant=true, confidence=0.98, reason="詢問帳號管理功能"
- 「忘記密碼」→ relevant=true, confidence=0.98, reason="詢問密碼重設功能"

請分類以下問題：
{question}"""

        try:
            result: IntentResult = await self.structured_llm.ainvoke(prompt)
            return {"relevant": result.relevant, "confidence": result.confidence, "reason": result.reason}
        except Exception as e:
            print(f"[Intent Classifier] ERROR: {type(e).__name__}: {e}")
            return {"relevant": True, "confidence": 0.5, "reason": f"分類器錯誤：{str(e)}"}

    async def is_relevant(self, question: str, history: list = None, confidence_threshold: float = 0.7) -> bool:
        """
        Simplified interface: returns True if question is relevant with high confidence.

        Args:
            question: User's input question
            history: Recent conversation turns, forwarded to classify()
            confidence_threshold: Minimum confidence to consider relevant (default: 0.7)

        Returns:
            True if question is relevant and confidence >= threshold
        """
        result = await self.classify(question, history)
        return result["relevant"] and result["confidence"] >= confidence_threshold

    def get_off_topic_message(self) -> str:
        """
        Get the standard response for off-topic questions.

        Returns:
            Pre-formatted message to show users when their question is off-topic
        """
        return """抱歉，我是碳管理系統（CarbonM）專屬助手，無法回答此問題。

我可以協助您：
• 系統操作指南（登入、密碼、權限、資料輸入）
• 盤查相關問題（活動數據、類別排放、廠區管理）
• 碳排放計算（排放係數、碳足跡、排放源定義）
• 環境法規（溫室氣體盤查、登錄表單、許可證）
• 報表與數據（匯出、查詢、統計）

請問您是否有系統相關問題需要協助？"""
