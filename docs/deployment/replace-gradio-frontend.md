# React frontend revision 驗證、切換與回復

2026-10-03 已依本手冊完成部署：`release/v1.0.2` 的 CI 成功，正式流量為 React revision `carbon-assistant-web-00016-mpd`（100%），preview tag 已移除。候選及正式 URL 驗收通過；使用者已於 2026-10-03 確認實體手機驗收完成。舊 Gradio revision `carbon-assistant-web-00014-pw9` 與 image 保留，可依第 5 節指令回復，尚未實際演練流量回復。

## 1. 保存當前狀態

```sh
project=carbon-rag-assistant-prod-2026
region=asia-east1
service=carbon-assistant-web
mkdir -p /tmp/rag-release

gcloud run services describe "$service" --project "$project" --region "$region" \
  --format='json(status.latestReadyRevisionName,status.traffic,status.url,spec.template.spec.containers[0].image)' \
  > /tmp/rag-release/before.json
```

確認目前舊 revision 名稱與 immutable image tag。2026-10-02 基線 image 是 `asia-east1-docker.pkg.dev/carbon-rag-assistant-prod-2026/carbon-assistant/carbon-assistant:bd9c42b652d556caccb66891c9f28d2051c042ea`。切換期間不要刪除該 image/revision。

清理後的新程式只有 React SPA，無須設定 `FRONTEND_MODE`。回復 Gradio 時使用保留的舊 revision/image。

## 2. 建置候選 image

以通過 review 的 commit SHA 作 tag，使用 Linux amd64（本機 Apple Silicon image 不直接作正式 image）。push 到 Artifact Registry 之後記錄 digest。

```sh
release_sha=$(git rev-parse HEAD)
image="asia-east1-docker.pkg.dev/$project/carbon-assistant/carbon-assistant:$release_sha"
gcloud auth configure-docker asia-east1-docker.pkg.dev --quiet
docker buildx build --platform linux/amd64 --tag "$image" --push .
```

## 3. 先固定舊流量，再建立 no-traffic revision

正式服務當前 traffic 使用 latestRevision。先固定到舊 revision，避免任何後續新 revision 意外接到流量。更新既有 service 以保留 runtime service account、secrets、GCS volumes、資源配置；不以新服務預設值覆蓋。若帶有自訂 `APP_PUBLIC_ORIGIN`，需先檢查其與候選 tag URL 的同源驗證是否相容，不能繞過 CSRF。

```sh
old_revision=carbon-assistant-web-00014-pw9  # 依 before.json 重新確認
suffix="spa-$(git rev-parse --short=8 HEAD)"
gcloud run services update-traffic "$service" --project "$project" --region "$region" \
  --to-revisions="$old_revision=100"
gcloud run services update "$service" --project "$project" --region "$region" \
  --image="$image" --update-env-vars=MAX_UPLOAD_BYTES=26214400 \
  --revision-suffix="$suffix" --no-traffic --tag=spa-preview
```

重新 describe，確認正式 service URL 仍為舊 revision 100%、新 revision ready。從 status.traffic 取得 `spa-preview` URL；不要猜網址。此 revision 仍會讀取既有資料與使用真實 provider，完整聊天及文件同步操作需明確授權。候選上傳會修改共用知識庫，不以任意測試檔驗證正式 sync。

## 4. 驗收候選 URL

- `/health` 200；`/`、`/login`、`/admin` 回 compiled SPA，hashed JS/CSS 可載入。
- 未登入受保護 API 回安全 JSON 401，未知 `/api/*` 回 JSON 404。
- 登入、同源 cookie mutation、聊天各事件與來源、history restore/clear、登出正常。
- 手機中文輸入、停止接收、深淺模式、來源閱讀與捲動正常。
- 檢查 Cloud Run logs 的例外與失敗率，不在文件或 PR 貼出 secrets、session 或私人對話。
- 保留原 service URL、舊 revision 及 rollback 指令，記錄驗收時間、candidate revision/image digest。

## 5. 切換與立即回復

確定 review、候選驗收與手機確認皆完成後，將流量指向明確的新 revision，不採 latest alias。切換後再驗證正式 URL。

```sh
new_revision="$service-$suffix"
gcloud run services update-traffic "$service" --project "$project" --region "$region" \
  --to-revisions="$new_revision=100"
```

如出現登入、聊天、上傳或資源錯誤，立即回復：

```sh
gcloud run services update-traffic "$service" --project "$project" --region "$region" \
  --to-revisions="$old_revision=100"
```

再次確認 100% 舊 revision 與 `/health`、登入頁，記錄回復證據。完成測試後可移除 preview tag（不刪 revision）：

```sh
gcloud run services update-traffic "$service" --project "$project" --region "$region" \
  --remove-tags=spa-preview
```

## 6. 與 Terraform / CI 對齊

`.github/workflows/deploy.yml` 只對 `release/**` push 啟動，會以 Terraform 更新 Service 與 sync Job。不可把 release branch push 當作無流量預览。手動候選測試後，下次發布前需審查 Terraform plan 的 image 與 traffic，避免覆蓋回復狀態；不盲目執行 apply。Gradio 原始碼及依賴在 8.2 完成後才依 8.3 移除，舊 image 保留以支援回復。
