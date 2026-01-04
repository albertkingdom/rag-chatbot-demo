"""
Unit tests for IntentClassifier.
Tests the ability to distinguish between system-related and off-topic questions.
"""
import pytest
import os
from src.intent_classifier import IntentClassifier


# Skip tests if GOOGLE_API_KEY is not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set"
)


@pytest.fixture
def classifier():
    """Create an IntentClassifier instance for testing."""
    return IntentClassifier()


class TestIntentClassifier:
    """Test suite for IntentClassifier."""

    @pytest.mark.asyncio
    async def test_system_related_questions(self, classifier):
        """Test that system-related questions are correctly identified."""

        system_questions = [
            "如何重設密碼？",
            "我該如何登入我的帳號？",
            "如何新增產品碳排放資料？",
            "移動式燃料源排放代表什麼？",
            "如何切換多廠區邊界？",
            "溫室氣體盤查登錄表單在哪裡？",
            "類別 4.3 排放如何輸入？",
            "固定源許可證是什麼？",
        ]

        for question in system_questions:
            result = await classifier.classify(question)

            assert "relevant" in result, f"Missing 'relevant' key for: {question}"
            assert "confidence" in result, f"Missing 'confidence' key for: {question}"

            # System-related questions should be marked as relevant
            assert result["relevant"] is True, f"Failed for: {question}, result: {result}"
            assert result["confidence"] >= 0.7, f"Low confidence for: {question}, result: {result}"

    @pytest.mark.asyncio
    async def test_off_topic_questions(self, classifier):
        """Test that off-topic questions are correctly rejected."""

        off_topic_questions = [
            "今天天氣如何？",
            "推薦台北好吃的餐廳",
            "幫我寫一首詩",
            "誰是台灣總統？",
            "1+1等於多少？",
            "如何學習 Python？",
            "請給我一個笑話",
            "什麼是區塊鏈？",
        ]

        for question in off_topic_questions:
            result = await classifier.classify(question)

            assert "relevant" in result, f"Missing 'relevant' key for: {question}"
            assert "confidence" in result, f"Missing 'confidence' key for: {question}"

            # Off-topic questions should be marked as not relevant
            assert result["relevant"] is False, f"Failed for: {question}, result: {result}"
            assert result["confidence"] >= 0.7, f"Low confidence for: {question}, result: {result}"

    @pytest.mark.asyncio
    async def test_edge_cases(self, classifier):
        """Test edge cases and ambiguous questions."""

        edge_cases = [
            ("你好", False),  # Simple greeting
            ("謝謝", False),  # Thank you
            ("CarbonM 是什麼？", True),  # Direct system name mention
            ("碳排放是什麼？", True),  # Domain knowledge (should be relevant)
            ("如何計算碳足跡？", True),  # Domain-specific calculation
        ]

        for question, expected_relevant in edge_cases:
            result = await classifier.classify(question)

            assert result["relevant"] == expected_relevant, \
                f"Failed for: {question}, expected: {expected_relevant}, got: {result}"

    @pytest.mark.asyncio
    async def test_is_relevant_method(self, classifier):
        """Test the simplified is_relevant() method."""

        # Should return True for system questions
        assert await classifier.is_relevant("如何重設密碼？") is True

        # Should return False for off-topic questions
        assert await classifier.is_relevant("今天天氣如何？") is False

    def test_get_off_topic_message(self, classifier):
        """Test that off-topic message is properly formatted."""

        message = classifier.get_off_topic_message()

        # Should contain key information
        assert "抱歉" in message
        assert "CarbonM" in message or "碳管理系統" in message
        assert "協助" in message or "幫助" in message

        # Should mention main features
        assert any(keyword in message for keyword in ["操作", "盤查", "計算", "法規", "報表"])

    @pytest.mark.asyncio
    async def test_confidence_threshold(self, classifier):
        """Test that confidence threshold works correctly."""

        # Clear system question should have high confidence
        result = await classifier.classify("如何登入系統？")
        assert result["confidence"] >= 0.8

        # Use is_relevant with different thresholds
        high_threshold = await classifier.is_relevant("如何登入系統？", confidence_threshold=0.9)
        low_threshold = await classifier.is_relevant("如何登入系統？", confidence_threshold=0.5)

        # At least one should pass
        assert low_threshold is True

    @pytest.mark.asyncio
    async def test_result_structure(self, classifier):
        """Test that classify() returns the correct structure."""

        result = await classifier.classify("如何重設密碼？")

        # Check all required keys are present
        assert "relevant" in result
        assert "confidence" in result
        assert "reason" in result

        # Check types
        assert isinstance(result["relevant"], bool)
        assert isinstance(result["confidence"], float)
        assert isinstance(result["reason"], str)

        # Check value ranges
        assert 0.0 <= result["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_classifier_initialization_without_api_key():
    """Test that classifier raises error when API key is missing."""

    # Temporarily remove API key
    original_key = os.environ.get("GOOGLE_API_KEY")
    if "GOOGLE_API_KEY" in os.environ:
        del os.environ["GOOGLE_API_KEY"]

    try:
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            IntentClassifier()
    finally:
        # Restore API key
        if original_key:
            os.environ["GOOGLE_API_KEY"] = original_key


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
