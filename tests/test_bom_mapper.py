from bom_mapper import classify_bom_headers

# Test with a CSV file (assuming bom1.xlsx.csv exists)
result = classify_bom_headers('test_bom.xlsx.csv')
print("Classification result:")
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
