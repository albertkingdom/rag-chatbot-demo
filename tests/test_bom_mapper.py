"""Manual smoke script for BOM header classification.

Not a pytest test (no test_* functions) — run it directly to exercise
classify_bom_headers against a sample file. Imports from src.bom_mapper
(lazy LLM via src.services.get_llm).

Usage:
    OPENROUTER_API_KEY=... venv/bin/python tests/test_bom_mapper.py
"""
import json

from src.bom_mapper import classify_bom_headers

# Test with a CSV file (assuming bom1.xlsx.csv exists)
result = classify_bom_headers("test_bom.xlsx.csv")
print("Classification result:")
print(json.dumps(result, indent=2, ensure_ascii=False))
