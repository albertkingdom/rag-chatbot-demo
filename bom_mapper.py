
import csv
import json
from collections import defaultdict

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

def classify_bom_headers(file_path):
    """
    讀取 CSV 檔案，分析其欄位，並將其分類到預定義的系統類別中。

    Args:
        file_path (str): The absolute path to the CSV file.

    Returns:
        dict: A dictionary containing the classification results.
    """
    try:
        with open(file_path, mode='r', encoding='utf-8-sig') as infile:
            reader = csv.reader(infile)
            try:
                headers = next(reader)
            except StopIteration:
                return {"error": "File is empty or not a valid CSV."}
    except FileNotFoundError:
        return {"error": f"File not found at {file_path}"}
    except Exception as e:
        return {"error": f"An error occurred while reading the file: {e}"}

    matched_categories = defaultdict(list)
    unrecognized_headers = []
    
    # 將系統欄位名稱轉換為中文，方便輸出
    system_categories_chinese = list(SYSTEM_CATEGORIES.values())
    
    found_system_categories = set()

    for header in headers:
        #正規化 header，去除前後空格並轉為小寫以便比對
        normalized_header = header.strip().lower()
        
        # 優先使用完全匹配的關鍵字字典
        category = HEADER_MAPPING.get(header, HEADER_MAPPING.get(normalized_header.capitalize()))

        if category:
            # 找到了對應的系統類別
            chinese_category = SYSTEM_CATEGORIES.get(category)
            if chinese_category:
                matched_categories[chinese_category].append(header)
                found_system_categories.add(chinese_category)
        else:
            # TODO: 在這裡可以加入語意相似度比對或 LLM 作為 fallback
            unrecognized_headers.append(header)

    # 找出哪些系統要求的欄位沒有被匹配到
    unmatched_categories = [cat for cat in system_categories_chinese if cat not in found_system_categories]

    # 根據需求文件格式化輸出
    # "Matched categories with wrong headers" 的判斷比較複雜，需要語意分析，在此暫時留空
    result = {
        "Matched categories with its headers": dict(matched_categories),
        "Unmatched categories": unmatched_categories,
        "Matched categories with wrong headers": {}, # Placeholder for more advanced logic
        "Unmatched bom headers": unrecognized_headers
    }

    return result


