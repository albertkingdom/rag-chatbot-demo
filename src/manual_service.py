"""Manual upload validation and local/GCP background job adapters."""

import base64
import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from .config import DATA_SOURCE_DIR

JOB_RUNNER = os.environ.get("JOB_RUNNER", "local")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

_ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
}


class ManualUploadError(ValueError):
    pass


class ManualTooLargeError(ManualUploadError):
    pass


def _encode_gcp_job(execution_name: str) -> str:
    encoded = base64.urlsafe_b64encode(execution_name.encode("utf-8")).decode("ascii")
    return f"gcp_{encoded.rstrip('=')}"


def _decode_gcp_job(job_id: str) -> str:
    if not job_id.startswith("gcp_"):
        raise ManualUploadError("Invalid job id")
    encoded = job_id[4:]
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManualUploadError("Invalid job id") from exc


async def save_manual(upload: UploadFile) -> Path:
    original_name = Path(upload.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in _ALLOWED_CONTENT_TYPES:
        raise ManualUploadError("只支援 PDF、XLSX、CSV 檔案")
    if upload.content_type not in _ALLOWED_CONTENT_TYPES[suffix]:
        raise ManualUploadError("檔案類型與副檔名不符")

    save_dir = Path(DATA_SOURCE_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)
    final_path = save_dir / f"{uuid.uuid4().hex}-{original_name}"
    temp_path = save_dir / f".{uuid.uuid4().hex}.upload"
    total = 0
    try:
        with temp_path.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ManualTooLargeError(
                        f"檔案不可超過 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                    )
                output.write(chunk)
        os.replace(temp_path, final_path)
        return final_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def enqueue_sync() -> str:
    if JOB_RUNNER == "gcp":
        from google.cloud import run_v2

        project_id = os.environ.get("GCP_PROJECT_ID", "")
        region = os.environ.get("GCP_REGION", "asia-east1")
        job_name = os.environ.get("SYNC_JOB_NAME", "sync-job")
        client = run_v2.JobsClient()
        operation = client.run_job(
            name=f"projects/{project_id}/locations/{region}/jobs/{job_name}"
        )
        return _encode_gcp_job(operation.metadata.name)

    from rq import Queue
    from .build_vector_store import sync_vector_store
    from .services import get_redis_conn

    job = Queue(connection=get_redis_conn()).enqueue(sync_vector_store, job_timeout="1h")
    return job.id


def get_sync_status(job_id: str) -> dict[str, str]:
    if job_id.startswith("gcp_"):
        from google.api_core.exceptions import NotFound
        from google.cloud import run_v2

        try:
            execution = run_v2.ExecutionsClient().get_execution(
                name=_decode_gcp_job(job_id)
            )
        except NotFound as exc:
            raise KeyError(job_id) from exc
        if execution.succeeded_count and execution.succeeded_count > 0:
            return {"jobId": job_id, "status": "succeeded", "message": "知識庫已更新"}
        if execution.failed_count and execution.failed_count > 0:
            return {"jobId": job_id, "status": "failed", "message": "同步失敗，請檢查後台日誌"}
        return {"jobId": job_id, "status": "running", "message": "正在更新知識庫"}

    from rq.exceptions import NoSuchJobError
    from rq.job import Job
    from .services import get_redis_conn

    try:
        status = Job.fetch(job_id, connection=get_redis_conn()).get_status(refresh=True)
    except NoSuchJobError as exc:
        raise KeyError(job_id) from exc
    # RQ 2.x may return a JobStatus enum while older releases return a string.
    status = getattr(status, "value", status)
    normalized = {
        "queued": "queued",
        "deferred": "queued",
        "scheduled": "queued",
        "started": "running",
        "finished": "succeeded",
        "failed": "failed",
        "stopped": "failed",
        "canceled": "failed",
    }.get(status, "running")
    messages = {
        "queued": "已進入佇列",
        "running": "正在更新知識庫",
        "succeeded": "知識庫已更新",
        "failed": "同步失敗，請檢查後台日誌",
    }
    return {"jobId": job_id, "status": normalized, "message": messages[normalized]}
