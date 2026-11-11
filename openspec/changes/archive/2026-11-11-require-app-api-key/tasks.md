## 1. 驗證初始化行為（Auth-disabled development mode）

- [x] 1.1 依 Auth-disabled development mode，先在 `tests/test_access_control.py` 以真實 mount_auth 取代缺金鑰時預期停用驗證的舊測試並加入失敗回歸測試，涵蓋明確啟用與預設啟用各搭配未設定及空金鑰，確認因未拋出 RuntimeError 而失敗。另測缺金鑰且限流或 session 數值無效時仍優先回報缺金鑰。用 Redis provider stub 隔離外部服務，並用真實掛載取代手動發出警告的測試，覆蓋明確停用且無金鑰時的警告、無憑證請求與健康檢查。
- [x] 1.2 修改 `src/access_control.py` 的設定驗證，重跑同一測試檔確認通過。先加入獨立子程序匯入 src.app 的案例，重現重複解析驗證數值時診斷被蓋掉的問題，再移除 `src/config.py` 中無呼叫者的驗證設定；確認子程序失敗訊息點名 APP_API_KEY。暫時還原關鍵修正並驗證相同回歸案例失敗，再恢復修正，重跑單檔與全部後端測試。記錄實際結果以供 fix commit 使用。

## 2. 評估工具相容性

- [x] 2.1 修改 `tests/evaluate_rag.py` 與 `tests/generate_rag_eval_data.py` 的 provider 匯入，使評估工具能在無 HTTP 金鑰時載入。用 env -u APP_API_KEY AUTH_ENABLED=true python -m tests.generate_rag_eval_data --help 和離線 stub 驗證評估腳本的取樣與 judge provider 入口，確認兩者皆未載入 src.app。

## 3. 設定文件

- [x] 3.1 更新 `README.md`、`.env.example` 和 `architecture.md` 的限流參數來源，核對三處與規格一致，指出預設啟用時金鑰必填，以及本機開發可明確停用驗證。
