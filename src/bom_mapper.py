import csv
import json
import os
from collections import defaultdict
from typing import Dict, List, Union
import traceback
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from fuzzywuzzy import fuzz
import pandas as pd
from pydantic import BaseModel, Field
from langsmith import traceable

# 初始化 LangChain LLM
API_KEY = os.getenv("GOOGLE_API_KEY")
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, google_api_key=API_KEY) if API_KEY else None

# 根據 Product Requirements Document.docx.pdf 定義系統的五個標準欄位
SYSTEM_CATEGORIES = {
    "Company Part No.": "公司料號",
    "Part Description": "料號描述",
    "Part Net Weight": "料號淨重",
    "Part Gross Weight": "料號毛重",
    "Net/Gross Unit": "淨毛重單位"
}

# 建立一個關鍵字映射字典，用於規則比對
HEADER_MAPPING = {
    # English Headers
    "Comp_item": "Company Part No.",
    "Part Number": "Company Part No.",
    "Part No.": "Company Part No.",
    "serial number": "Company Part No.",
    "子件代碼": "Company Part No.",
    "物料型號": "Company Part No.",
    "Description": "Part Description",
    "Size/Dimension": "Part Description",
    "Comment": "Part Description",
    "Designator": "Part Description",
    "Spec": "Part Description",
    "Remark": "Part Description",
    "Net weight": "Part Net Weight",
    "Net Weight": "Part Net Weight",
    "Gross weight": "Part Gross Weight",
    "Gross Weight": "Part Gross Weight",
    "unit": "Net/Gross Unit",
    "Unit": "Net/Gross Unit",
    "Net weight unit": "Net/Gross Unit",
    "Gross weight unit": "Net/Gross Unit",

    # Chinese Headers from bom2.xlsx and test_bom.xlsx
    "元件料號": "Company Part No.",
    "料號": "Company Part No.",
    "物料描述": "Part Description",
    "材料規格": "Part Description",
    "規格": "Part Description",
    "備註": "Part Description",
    "料號描述": "Part Description",
    "淨重": "Part Net Weight",
    "料號淨重": "Part Net Weight",
    "毛重": "Part Gross Weight",
    "料號毛重": "Part Gross Weight",
    "單位": "Net/Gross Unit",
    "重量單位": "Net/Gross Unit",
    "淨毛重單位": "Net/Gross Unit",
}

# --- Pydantic Models for Structured LLM Output ---
class ClassifiedHeader(BaseModel):
    header: str = Field(description="The original header text.")
    category: str = Field(description="The classified category (or 'None' if no fit).")

class ClassificationResponse(BaseModel):
    classifications: List[ClassifiedHeader] = Field(default_factory=list, description="A list of classified headers.")

class VerificationResult(BaseModel):
    header: str = Field(description="The original header text.")
    category: str = Field(description="The category it was matched with.")
    correct: bool = Field(description="A boolean value indicating if the match is correct.")

class VerificationResponse(BaseModel):
    verifications: List[VerificationResult] = Field(description="A list of verification results.")

# -----------------------------------------------------

def fuzzy_match_header(header: str, mapping: dict, threshold=80) -> Union[str, None]:
    """使用模糊匹配找到最相似的 header"""
    best_match = None
    best_score = 0

    for key, category in mapping.items():
        score = fuzz.ratio(header.lower(), key.lower())
        if score > best_score and score >= threshold:
            best_match = key
            best_score = score

    if best_match:
        return mapping[best_match]
    return None

def llm_batch_classify_headers(headers: List[str], categories: List[str]) -> Dict[str, str]:
    """使用 LLM 批次分類 headers，並強制使用定義好的 Pydantic Schema。"""
    if not llm or not headers:
        return {}

    prompt = (
        f"You are an expert in BOM (Bill of Materials) data processing. "
        f"Classify each of the following BOM headers into the most fitting category. "
        f"The available categories are: {', '.join(categories)}. "
        f"If a header does not fit any category, classify it as 'None'. **Crucially, ensure that every header provided in the input list is present in your output 'classifications' list. "
        f"Return your response as a single JSON object with a key named 'classifications'. The value of 'classifications' should be a list of objects, where each object has a 'header' field (the original header) and a 'category' field (the matched category or 'None'). "
        f"Ensure the JSON is well-formed.\n\n"
        f"Here is an example:\n"
        f"Input Headers: [\"Part Number\", \"Description\", \"Item Code\", \"Product Spec\"]\n"
        f"Output: {{ \"classifications\": [{{ \"header\": \"Part Number\", \"category\": \"Company Part No.\" }}, {{ \"header\": \"Description\", \"category\": \"Part Description\" }}, {{ \"header\": \"Item Code\", \"category\": \"Company Part No.\" }}, {{ \"header\": \"Product Spec\", \"category\": \"Part Description\" }}] }}\n\n"
        f"Headers to classify: {json.dumps(headers, ensure_ascii=False)}"
    )

    try:
        structured_llm = llm.with_structured_output(ClassificationResponse, method="function_calling")
        response = structured_llm.invoke([HumanMessage(content=prompt)])
        return {item.header: item.category for item in response.classifications if item.category and item.category.lower() != 'none'}
    except Exception as e:
        print(f"--- LLM BATCH CLASSIFICATION ERROR (Outer) ---")
        print(f"Full Exception: {e}")
        print(f"--------------------------------------")
        return {}

def llm_batch_verify_headers(llm_matched: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """使用 LLM 批次驗證匹配是否正確，並強制使用定義好的 Pydantic Schema。"""
    wrong_headers = defaultdict(list)
    if not llm or not llm_matched:
        return dict(wrong_headers)

    verification_batch = []
    for english_cat, headers in llm_matched.items(): # Changed chinese_cat to english_cat
        for header in headers:
            verification_batch.append({"header": header, "category": english_cat}) # Use english_cat

    if not verification_batch:
        print(f"--- DEBUG: llm_batch_verify_headers returning empty dict due to empty verification_batch ---")
        return dict(wrong_headers)

    prompt = (
        f"You are a quality assurance expert for BOM data. "
        f"For each item in the following list, determine if the 'header' accurately represents the 'category'. "
        f"Provide a boolean 'correct' field for each item.\n\n"
        f"Here is an example:\n"
        f"Input Items: [{{ \"header\": \"Part Number\", \"category\": \"Company Part No.\" }}, {{ \"header\": \"Description\", \"category\": \"Part Description\" }}]\n"
        f"Output: {{ \"verifications\": [{{ \"header\": \"Part Number\", \"category\": \"Company Part No.\", \"correct\": true }}, {{ \"header\": \"Description\", \"category\": \"Part Description\", \"correct\": true }}] }}\n\n"
        f"Items to verify: {json.dumps(verification_batch, ensure_ascii=False)}"
    )

    try:
        structured_llm = llm.with_structured_output(VerificationResponse, method="function_calling")
        response = structured_llm.invoke([HumanMessage(content=prompt)])
        for result in response.verifications:
            if not result.correct:
                wrong_headers[result.category].append(result.header)
        return dict(wrong_headers)

    except Exception as e:
        print(f"--- LLM BATCH VERIFICATION ERROR ---")
        print(f"Full Exception: {e}")
        print(f"--------------------------------------")
        print(f"--- DEBUG: llm_batch_verify_headers returning empty dict due to exception ---")
        return dict(wrong_headers)

def get_headers_from_file(file_path: str) -> List[str]:
    """Reads headers from a CSV or XLSX file."""
    if file_path.lower().endswith('.csv'):
        with open(file_path, mode='r', encoding='utf-8-sig') as infile:
            reader = csv.reader(infile)
            try:
                return next(reader)
            except StopIteration:
                raise ValueError("File is empty or not a valid CSV.")
    elif file_path.lower().endswith('.xlsx'):
        df = pd.read_excel(file_path, nrows=0) # Efficiently read only the header row
        return df.columns.tolist()
    else:
        raise ValueError("Unsupported file type. Please upload a .csv or .xlsx file.")


def get_headers_from_file(file_path: str) -> List[str]:
    """Reads headers from a CSV or XLSX file."""
    if file_path.lower().endswith('.csv'):
        with open(file_path, mode='r', encoding='utf-8-sig') as infile:
            reader = csv.reader(infile)
            try:
                return next(reader)
            except StopIteration:
                raise ValueError("File is empty or not a valid CSV.")
    elif file_path.lower().endswith('.xlsx'):
        df = pd.read_excel(file_path, nrows=0) # Efficiently read only the header row
        return df.columns.tolist()
    else:
        raise ValueError("Unsupported file type. Please upload a .csv or .xlsx file.")

@traceable(name="BOM Header Classification")
def classify_bom_headers(file_path: str) -> Dict:
    """
    Reads a file, analyzes its headers, and classifies them into predefined system categories using a batched approach.
    """
    try:
        headers = get_headers_from_file(file_path)
    except FileNotFoundError:
        return {"error": f"File not found at {file_path}"}
    except Exception as e:
        return {"error": f"An error occurred while reading the file: {e}"}

    try:
        matched_categories = defaultdict(list)
        unrecognized_headers = []
        headers_to_llm = []

        # Stage 1 & 2: Exact and Fuzzy Matching
        for header in headers:
            normalized_header = header.strip()
            if not normalized_header:
                continue

            category = HEADER_MAPPING.get(normalized_header, HEADER_MAPPING.get(normalized_header.capitalize()))

            if category:
                chinese_category = SYSTEM_CATEGORIES.get(category)
                if chinese_category:
                    matched_categories[chinese_category].append(normalized_header)
            else:
                fuzzy_category = fuzzy_match_header(normalized_header, HEADER_MAPPING)
                if fuzzy_category:
                    chinese_category = SYSTEM_CATEGORIES.get(fuzzy_category)
                    if chinese_category:
                        matched_categories[chinese_category].append(normalized_header)
                else:
                    headers_to_llm.append(normalized_header)

        # Stage 3: Batch LLM Classification
        english_categories = list(SYSTEM_CATEGORIES.keys())
        llm_classifications = llm_batch_classify_headers(headers_to_llm, english_categories)
        
        llm_matched_categories = defaultdict(list)
        for header, llm_category in llm_classifications.items():
            chinese_category = SYSTEM_CATEGORIES.get(llm_category)
            if chinese_category:
                matched_categories[chinese_category].append(header)
                llm_matched_categories[llm_category].append(header)
            else:
                unrecognized_headers.append(header)

        # Add remaining headers that LLM didn't classify to unrecognized
        classified_by_llm = set(llm_classifications.keys())
        for header in headers_to_llm:
            if header not in classified_by_llm:
                unrecognized_headers.append(header)

        # Stage 4: Batch LLM Verification
        wrong_headers = llm_batch_verify_headers(llm_matched_categories)

        # Final processing: remove wrong headers and find unmatched system categories
        for cat, wrongs in wrong_headers.items():
            if cat in matched_categories:
                matched_categories[cat] = [h for h in matched_categories[cat] if h not in wrongs]
                # Add wrongly matched headers to the final unrecognized list
                unrecognized_headers.extend(wrongs)

        system_categories_chinese = list(SYSTEM_CATEGORIES.values())
        found_system_categories = set(matched_categories.keys())
        unmatched_categories = [cat for cat in system_categories_chinese if cat not in found_system_categories]

        result = {
            "Matched categories with its headers": {k: v for k, v in matched_categories.items() if v},
            "Unmatched categories": unmatched_categories,
            "Matched categories with wrong headers": wrong_headers,
            "Unmatched bom headers": sorted(list(set(unrecognized_headers)))
        }

        return result
    except Exception as e:
        print(f"--- CRITICAL ERROR IN CLASSIFY_BOM_HEADERS ---")
        print(f"Full Traceback: {traceback.format_exc()}")
        print(f"----------------------------------------------")
        return {"error": f"A critical error occurred: {e}"}