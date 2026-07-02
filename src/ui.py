"""Gradio UI definition and BOM/upload handlers.

The `demo` Blocks instance is defined at module level and consumed by
src/app.py via `gr.mount_gradio_app(app, demo, path="/")`. Handlers obtain
RAG/business functions from src.rag_pipeline and providers from src.services.
"""

import asyncio
import os
import shutil
import traceback
from pathlib import Path

import gradio as gr
from rq import Queue
from rq.job import Job

from .bom_mapper import classify_bom_headers
from .build_vector_store import sync_vector_store
from .config import DATA_SOURCE_DIR
from .rag_pipeline import chat_stream
from .services import get_redis_conn

# Task execution mode: "local" uses RQ + Redis worker; "gcp" calls the
# Cloud Run Jobs API. Defaults to "local" so docker-compose keeps working.
JOB_RUNNER = os.environ.get("JOB_RUNNER", "local")

# GCP Cloud Run Job coordinates (only used when JOB_RUNNER == "gcp").
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "asia-east1")
GCP_JOB_NAME = os.environ.get("SYNC_JOB_NAME", "sync-job")

# RQ queue is only needed in local mode. Lazily built on first use so that
# importing this module (and starting the app in gcp mode) does not open a
# Redis connection.
_q = None


def _get_queue():
    """Lazily build the RQ queue on the shared Redis connection (local mode)."""
    global _q
    if _q is None:
        _q = Queue(connection=get_redis_conn())
    return _q


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def bom_mapper_func(file):
    """Wrapper for BOM mapping; disables the button during execution."""
    if file is None:
        return {"error": "Please upload a file first."}, gr.update(interactive=True)

    yield {"status": "Processing..."}, gr.update(interactive=False)

    try:
        result = classify_bom_headers(file.name)
        yield result, gr.update(interactive=True)
    except Exception as e:
        traceback.print_exc()
        yield {"error": str(e)}, gr.update(interactive=True)


def upload_manual_func(file):
    """Saves the file and enqueues a sync job, returning the job ID."""
    if file is None:
        return "No file uploaded.", None

    save_dir = Path(DATA_SOURCE_DIR)
    os.makedirs(save_dir, exist_ok=True)
    save_path = save_dir / Path(file.name).name

    try:
        shutil.copy(file.name, save_path)
        if JOB_RUNNER == "gcp":
            from google.cloud import run_v2

            jobs_client = run_v2.JobsClient()
            job_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/jobs/{GCP_JOB_NAME}"
            operation = jobs_client.run_job(name=job_name)
            execution_name = operation.metadata.name
            return f"File uploaded. Sync execution '{execution_name}' started.", execution_name
        else:
            job = _get_queue().enqueue(sync_vector_store, job_timeout="1h")
            return f"File uploaded. Sync job '{job.id}' enqueued.", job.id
    except Exception as e:
        return f"Error: {str(e)}", None


async def poll_status(job_id):
    """Polls the job status and yields updates."""
    if not job_id:
        yield "Job ID not found. Please upload again."
        return

    if JOB_RUNNER == "gcp":
        from google.cloud import run_v2
        from google.api_core.exceptions import NotFound

        executions_client = run_v2.ExecutionsClient()
        while True:
            try:
                execution = executions_client.get_execution(name=job_id)
                if execution.succeeded_count and execution.succeeded_count > 0:
                    yield "同步成功！知識庫已更新。"
                    break
                elif execution.failed_count and execution.failed_count > 0:
                    yield "任務失敗！請檢查後台日誌。"
                    break
                else:
                    yield "任務執行中，系統正在處理您的文件..."
                await asyncio.sleep(3)
            except NotFound as e:
                yield f"無法獲取任務狀態 {job_id}: {e}"
                break
            except Exception as e:
                yield f"無法獲取任務狀態 {job_id}: {e}"
                break
        return

    while True:
        try:
            job = Job.fetch(job_id, connection=get_redis_conn())
            status = job.get_status(refresh=True)

            status_map = {
                "queued": f"Job {job.id}: 已進入佇列，正在等待執行...",
                "started": f"Job {job.id}: 任務執行中，系統正在處理您的文件...",
                "finished": f"Job {job.id}: 同步成功！知識庫已更新。",
                "failed": f"Job {job.id}: 任務失敗！請檢查後台日誌。",
            }

            message = status_map.get(status, f"Job {job.id}: 未知狀態 ({status})")
            yield message

            if status in ["finished", "failed", "canceled", "stopped"]:
                break

            await asyncio.sleep(1)

        except Exception as e:
            yield f"無法獲取任務狀態 {job_id}: {e}"
            break


# ---------------------------------------------------------------------------
# Gradio Blocks UI
# ---------------------------------------------------------------------------

with gr.Blocks(theme=gr.themes.Soft(), title="Carbon Assistant App") as demo:
    gr.Markdown("<h1>Carbon Assistant & BOM Mapping Tool</h1>")

    with gr.Tab("RAG Chatbot"):
        gr.ChatInterface(
            chat_stream,
            chatbot=gr.Chatbot(height=500),
            textbox=gr.Textbox(placeholder="詢問碳管理系統相關問題...", container=False, scale=7),
            title="碳管理系統智慧助手",
            description="基於操作手冊的 RAG 問答系統，支援多輪對話",
            examples=[
                "忘記密碼怎麼辦？",
                "如何在多廠區管理中切換邊界？",
                "什麼是固定式燃料源排放？",
                "報告和清冊的格式是什麼？",
                "系統有哪些角色？",
                "門檻值設定該如何填寫？",
            ],
        )

    with gr.Tab("BOM Header Mapper"):
        with gr.Row():
            bom_input = gr.File(label="Upload BOM file (.csv)")
            bom_output = gr.JSON(label="Mapping Result")
        bom_button = gr.Button("Map Headers")

        bom_button.click(
            bom_mapper_func,
            inputs=bom_input,
            outputs=[bom_output, bom_button],
        )

    with gr.Tab("Admin: Upload Manual"):
        job_id_state = gr.State(None)

        with gr.Row():
            manual_input = gr.File(label="Upload User Manual (.pdf, .xlsx, .csv)")
            with gr.Column():
                manual_output = gr.Textbox(label="Upload Status", interactive=False, lines=2, max_lines=4)
                sync_status_output = gr.Textbox(label="Sync Process Status", interactive=False, lines=2, max_lines=4)

        manual_button = gr.Button("Upload and Enqueue Sync")

        upload_event = manual_button.click(
            upload_manual_func,
            inputs=manual_input,
            outputs=[manual_output, job_id_state],
        )

        upload_event.then(
            poll_status,
            inputs=job_id_state,
            outputs=sync_status_output,
        )
