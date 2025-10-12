import csv
import json
import os
from collections import defaultdict
from typing import Dict, List, Union

# 新增 LLM 和模糊匹配的 imports
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from fuzzywuzzy import fuzz
import pandas as pd

# 初始化 LangChain LLM
API_KEY = os.getenv("OPENAI_API_KEY")
llm = ChatOpenAI(model="gpt-4o-mini", openai_api_key=API_KEY) if API_KEY else None

# 根據 Product Requirements Document.docx.pdf 定義系統的五個標準欄位
SYSTEM_CATEGORIES = {
    "Company Part No.": "公司料號",
    "Part Description": "料號描述",
    "Part Net Weight": "料號淨重",
    "Part Gross Weight": "料號毛重",
    "Net/Gross Unit": "淨毛重單位"
}

# 建立一個關鍵字映射字典，用於規則比對
# 包含了 bom1.xlsx, bom2.xlsx, test_bom.xlsx 中出現的以及其他可能的欄位名稱
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

def llm_classify_header(header: str, categories: list[str]) -> Union[str, None]:
    """使用 LLM 分類 header"""
    if not llm:
        return None

    prompt = f"Classify the following BOM header to the best fitting category. Categories: {', '.join(categories)}. If no good fit, respond 'None'. Header: {header}"

    try:
        response = llm.invoke([HumanMessage(content=prompt)]).content.strip().lower()
        # print(f"LLM response for {header}: {response}")  # Debug print
        if response in [cat.lower() for cat in categories]:
            return next(cat for cat in categories if cat.lower() == response)
        elif "none" in response:
            return None
        else:
            # Try to find best fit
            for cat in categories:
                if cat.lower() in response:
                    return cat
        return None
    except Exception as e:
        print(f"LLM error: {e}")
        return None

def verify_matched_headers(llm_matched: dict) -> Dict[str, List[str]]:
    """使用 LLM 驗證匹配是否正確，找出錯誤匹配 - 只驗證 LLM 分類的 headers"""
    wrong_headers = defaultdict(list)

    if not llm:
        return dict(wrong_headers)

    for chinese_cat, headers in llm_matched.items():
        for header in headers:
            prompt = f"零部件表頭部 '{header}' 正確表示類別 '{chinese_cat}' 嗎？回答 '是' 或 '否'。"

            try:
                response = llm.invoke([HumanMessage(content=prompt)]).content.strip().lower()
                print(f"Verification for {header} in {chinese_cat}: {response}")  # Debug
                if '否' in response or 'no' in response:
                    wrong_headers[chinese_cat].append(header)
            except Exception as e:
                print(f"Verification error for {header}: {e}")

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

def classify_bom_headers(file_path: str) -> Dict:
    """
    Reads a file, analyzes its headers, and classifies them into predefined system categories.

    Args:
        file_path (str): The absolute path to the CSV or XLSX file.

    Returns:
        dict: A dictionary containing the classification results.
    """
    try:
        headers = get_headers_from_file(file_path)
    except FileNotFoundError:
        return {"error": f"File not found at {file_path}"}
    except Exception as e:
        return {"error": f"An error occurred while reading the file: {e}"}

    matched_categories = defaultdict(list)
    unrecognized_headers = []

    # 將系統欄位名稱轉換為英文用於 LLM
    english_categories = list(SYSTEM_CATEGORIES.keys())
    system_categories_chinese = list(SYSTEM_CATEGORIES.values())

    # 記錄 LLM 分類的 headers，以後驗證
    llm_matched_categories = defaultdict(list)

    for header in headers:
        # 正規化 header
        normalized_header = header.strip()

        # 1. 首先嘗試完全匹配的關鍵字字典
        category = HEADER_MAPPING.get(normalized_header)
        if not category:
            capitalized = normalized_header.capitalize()
            category = HEADER_MAPPING.get(capitalized)

        if category:
            # 找到了對應的系統類別
            chinese_category = SYSTEM_CATEGORIES.get(category)
            if chinese_category:
                matched_categories[chinese_category].append(normalized_header)
        else:
            # 2. 如果沒有匹配，使用模糊匹配
            fuzzy_category = fuzzy_match_header(normalized_header, HEADER_MAPPING)
            if fuzzy_category:
                chinese_category = SYSTEM_CATEGORIES.get(fuzzy_category)
                if chinese_category:
                    matched_categories[chinese_category].append(normalized_header)
            else:
                # 3. 如果還是沒有，使用 LLM 分類
                llm_category = llm_classify_header(normalized_header, english_categories)
                if llm_category:
                    chinese_category = SYSTEM_CATEGORIES.get(llm_category)
                    if chinese_category:
                        matched_categories[chinese_category].append(normalized_header)
                        llm_matched_categories[chinese_category].append(normalized_header)  # 記錄 LLM 分類的
                    else:
                        unrecognized_headers.append(normalized_header)
                else:
                    unrecognized_headers.append(normalized_header)

    # 4. 驗證 matched headers 是否正確 (只驗證 LLM 分類的)
    wrong_headers = verify_matched_headers(llm_matched_categories)

    # 移除錯誤匹配的 headers 並記錄下來
    for cat, wrongs in wrong_headers.items():
        if cat in matched_categories:
            matched_categories[cat] = [h for h in matched_categories[cat] if h not in wrongs]

    # 找出哪些系統要求的欄位沒有被匹配到
    found_system_categories = set(matched_categories.keys())
    unmatched_categories = [cat for cat in system_categories_chinese if cat not in found_system_categories]

    # 根據需求文件格式化輸出
    result = {
        "Matched categories with its headers": dict(matched_categories),
        "Unmatched categories": unmatched_categories,
        "Matched categories with wrong headers": wrong_headers,
        "Unmatched bom headers": unrecognized_headers
    }

    return result
