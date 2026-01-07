"""
Intent Classifier for Carbon Management System.
Filters out off-topic questions before entering the RAG pipeline.
"""
import os
import json
from typing import Dict, Any
from langchain_google_genai import ChatGoogleGenerativeAI
from langsmith import traceable


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

    def __init__(self, google_api_key: str = None):
        """
        Initialize the intent classifier.

        Args:
            google_api_key: Google API key for Gemini. If None, reads from environment.
        """
        api_key = google_api_key or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY must be provided or set in environment")

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,  # Deterministic for classification
            google_api_key=api_key,
            model_kwargs={
                "response_mime_type": "application/json"  # Force JSON output
            }
        )

    @traceable(name="Intent Classification")
    async def classify(self, question: str) -> Dict[str, Any]:
        """
        Classify whether a question is related to the carbon management system.

        Args:
            question: User's input question

        Returns:
            Dict containing:
                - relevant (bool): True if question is system-related
                - confidence (float): Confidence score 0.0-1.0
                - reason (str): Brief explanation of the classification

        Example:
            >>> classifier = IntentClassifier()
            >>> result = await classifier.classify("如何重設密碼？")
            >>> print(result)
            {'relevant': True, 'confidence': 0.98, 'reason': '詢問系統帳號管理功能'}
        """
        prompt = f"""{self.SYSTEM_TOPICS}

你是碳管理系統的問題分類器。判斷以下問題是否與碳管理系統相關。

分類範例：
- 「今天天氣如何？」→ {{"relevant": false, "confidence": 0.95, "reason": "詢問天氣資訊"}}
- 「如何重設密碼？」→ {{"relevant": true, "confidence": 0.98, "reason": "詢問帳號管理功能"}}
- 「忘記密碼」→ {{"relevant": true, "confidence": 0.98, "reason": "詢問密碼重設功能"}}

請分類以下問題，回答 JSON 格式（包含 relevant, confidence, reason 三個欄位）：
{question}"""

        try:
            response = await self.llm.ainvoke(prompt)
            response_text = response.content.strip()

            # Handle empty response
            if not response_text:
                print(f"[Intent Classifier] ERROR: Empty response from LLM")
                return {
                    "relevant": True,
                    "confidence": 0.5,
                    "reason": "分類器返回空回應"
                }

            # Try to extract JSON if wrapped in markdown code blocks
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)

            # Validate result structure
            if not all(key in result for key in ["relevant", "confidence"]):
                raise ValueError("Invalid response format from LLM")

            # Ensure types
            result["relevant"] = bool(result["relevant"])
            result["confidence"] = float(result["confidence"])
            result["reason"] = result.get("reason", "")

            return result

        except json.JSONDecodeError as e:
            print(f"[Intent Classifier] ERROR: Failed to parse JSON: {e}")
            print(f"[Intent Classifier] Raw response: {response_text if 'response_text' in locals() else 'N/A'}")
            # Fallback: conservatively classify as relevant to avoid false negatives
            return {
                "relevant": True,
                "confidence": 0.5,
                "reason": "分類器解析失敗，預設為相關"
            }
        except Exception as e:
            print(f"[Intent Classifier] ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            # Fail open: let question through to RAG pipeline
            return {
                "relevant": True,
                "confidence": 0.5,
                "reason": f"分類器錯誤：{str(e)}"
            }

    async def is_relevant(self, question: str, confidence_threshold: float = 0.7) -> bool:
        """
        Simplified interface: returns True if question is relevant with high confidence.

        Args:
            question: User's input question
            confidence_threshold: Minimum confidence to consider relevant (default: 0.7)

        Returns:
            True if question is relevant and confidence >= threshold
        """
        result = await self.classify(question)
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
