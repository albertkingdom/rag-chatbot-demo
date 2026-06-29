## ADDED Requirements

### Requirement: JOB_RUNNER environment variable controls task execution mode

`src/app.py` SHALL read the environment variable `JOB_RUNNER` at startup. When `JOB_RUNNER=local`, the app SHALL use RQ for task queuing. When `JOB_RUNNER=gcp`, the app SHALL use the Cloud Run Jobs API. The default value SHALL be `local` if the variable is not set.

The Redis connection (`conn`) SHALL be initialized in BOTH modes because the semantic cache (`PromptCacheService`) depends on it. Only the RQ `Queue` (`q = Queue(connection=conn)`) SHALL be gated to `JOB_RUNNER=local`. The `google-cloud-run` package SHALL be imported only when `JOB_RUNNER=gcp`.

#### Scenario: Local mode initializes RQ on startup

- **WHEN** the app starts with `JOB_RUNNER=local`
- **THEN** `conn = Redis.from_url(REDIS_URL)` and `q = Queue(connection=conn)` SHALL both be initialized and no Cloud Run Jobs client SHALL be created

#### Scenario: GCP mode initializes Redis but skips RQ queue

- **WHEN** the Service starts with `JOB_RUNNER=gcp`
- **THEN** `conn = Redis.from_url(REDIS_URL)` SHALL be initialized for the semantic cache, and no `rq.Queue` object SHALL be created for task queuing

#### Scenario: docker-compose sets local mode

- **WHEN** `docker compose up` starts the web service
- **THEN** the `JOB_RUNNER=local` environment variable in `docker-compose.yml` SHALL be present, ensuring RQ mode is active

### Requirement: sync_vector_store runs as Cloud Run Job in GCP mode

When `JOB_RUNNER=gcp`, the system SHALL execute `sync_vector_store` as a Cloud Run Job named `sync-job`. The Job SHALL have a task timeout of 3600 seconds.

#### Scenario: Upload triggers Cloud Run Job execution in GCP mode

- **WHEN** admin uploads a file and clicks "Upload and Enqueue Sync" with `JOB_RUNNER=gcp`
- **THEN** the `upload_manual_func` handler in `src/app.py` SHALL call `run_v2.JobsClient().run_job()` targeting the `sync-job` job and return the resulting execution name as the job identifier

##### Example: execution name format

- **GIVEN** GCP project `my-project`, region `asia-east1`, job name `sync-job`
- **WHEN** `run_job()` is called
- **THEN** the returned execution name SHALL follow the pattern `projects/my-project/locations/asia-east1/jobs/sync-job/executions/<execution-id>`

#### Scenario: Upload triggers RQ job in local mode

- **WHEN** admin uploads a file and clicks "Upload and Enqueue Sync" with `JOB_RUNNER=local`
- **THEN** the `upload_manual_func` handler in `src/app.py` SHALL call `q.enqueue(sync_vector_store, job_timeout='1h')` and return the RQ job id as the job identifier

#### Scenario: Job runs to completion and exits

- **WHEN** `sync_vector_store()` returns successfully in GCP mode
- **THEN** the Cloud Run Job execution status SHALL transition to `SUCCEEDED` and the container SHALL exit with code 0

#### Scenario: Job failure is recorded

- **WHEN** `sync_vector_store()` raises an unhandled exception in GCP mode
- **THEN** the Cloud Run Job execution status SHALL transition to `FAILED` and the exception SHALL appear in Cloud Logging

### Requirement: Cloud Run Job runs the sync entrypoint via container command override

The Cloud Run Job and the Cloud Run Service SHALL use the same Docker image. The `Dockerfile` `CMD` (`uvicorn src.app:app`) SHALL remain unchanged and is used by the Service. The Job SHALL override the container command at the resource level to run the sync entrypoint instead of the web server. The sync entrypoint SHALL reuse the existing `if __name__ == '__main__':` block in `src/build_vector_store.py`; no separate sync entry module SHALL be added.

#### Scenario: Job overrides the container command

- **WHEN** the `google_cloud_run_v2_job` resource (`sync-job`) is defined in Terraform
- **THEN** its container `command` SHALL be `["python", "-m", "src.build_vector_store"]`
- **AND** the `google_cloud_run_v2_service` resource SHALL NOT set `command`, inheriting the Dockerfile `CMD`

#### Scenario: Sync failure exits non-zero

- **WHEN** `sync_vector_store()` raises an exception inside the `__main__` block of `src/build_vector_store.py`
- **THEN** the process SHALL exit with a non-zero status (`sys.exit(1)`) so the Cloud Run Job execution is recorded as `FAILED` rather than `SUCCEEDED`

### Requirement: UI polls status using mode-appropriate client

The `poll_status` generator in `src/app.py` SHALL use different status sources depending on `JOB_RUNNER`.

#### Scenario: Status mapping from GCP to UI message

- **WHEN** `get_execution()` returns an execution object in GCP mode
- **THEN** the condition field SHALL map to UI messages as follows:

##### Example: status mapping table

| GCP execution condition | UI message displayed |
|---|---|
| `RUNNING` (or no terminal condition) | `任務執行中，系統正在處理您的文件...` |
| `SUCCEEDED` | `同步成功！知識庫已更新。` |
| `FAILED` | `任務失敗！請檢查後台日誌。` |

#### Scenario: Local mode polls RQ job status

- **WHEN** `poll_status(job_id)` is called with `JOB_RUNNER=local`
- **THEN** status SHALL be retrieved via `Job.fetch(job_id, connection=conn)` and mapped to the same UI messages as GCP mode

#### Scenario: Invalid execution name returns error message

- **WHEN** `get_execution()` raises a `NotFound` exception in GCP mode
- **THEN** `poll_status` SHALL yield `無法獲取任務狀態 <job_id>: <error>` and stop polling

### Requirement: requirements.txt supports both local and GCP modes

`requirements.txt` SHALL retain `rq` and `redis` (required for local mode) and SHALL add `google-cloud-run` (required for GCP mode). Both packages SHALL be present in the same image so that a single Docker image works in both environments.

#### Scenario: Single image runs in both environments

- **WHEN** the Docker image built from the same Dockerfile is run with `JOB_RUNNER=local`
- **THEN** RQ and Redis SHALL be importable and functional
- **WHEN** the same image is run with `JOB_RUNNER=gcp`
- **THEN** `google.cloud.run_v2` SHALL be importable and functional

### Requirement: BM25 index persisted via write-once version file and atomic pointer

`src/bm25_index.py` persistence SHALL write each rebuilt index to a write-once version file `bm25_corpus_<version>.json` (never overwriting an existing version) and SHALL update a pointer file `bm25_current.txt` atomically (temp file + `os.replace`) to reference the latest version. This replaces the current direct-overwrite `_persist()` and removes the partial-read race when the Service reads while the Job writes on shared storage.

#### Scenario: Sync writes a new version and updates the pointer

- **WHEN** `build_from_documents()` persists a rebuilt index in the Cloud Run Job
- **THEN** a new `bm25_corpus_<version>.json` SHALL be created without modifying prior version files, and `bm25_current.txt` SHALL be atomically updated to name the new file

#### Scenario: Reader never sees a partially written index

- **WHEN** the Service reads the index while the Job is mid-write
- **THEN** the Service SHALL only ever resolve a fully written version file (the pointer flips atomically after the version file is complete)

### Requirement: BM25 index reloads when the pointer changes

`src/bm25_index.py` SHALL expose a `reload_if_stale()` method that compares the loaded version against the current pointer and reloads (`_load()`) only when they differ. `get_bm25_index()` in `src/app.py` SHALL call it before returning the index.

#### Scenario: Index reloads after a new sync

- **WHEN** the pointer version differs from the in-memory loaded version
- **THEN** `reload_if_stale()` SHALL reload the index from the version file named by the pointer and update the in-memory version marker

#### Scenario: No reload when pointer unchanged

- **WHEN** the pointer version equals the in-memory loaded version
- **THEN** `reload_if_stale()` SHALL return without re-reading the index from storage

### Requirement: Old BM25 version files are pruned

The sync process SHALL retain only the most recent N BM25 version files and SHALL delete older ones after updating the pointer, preventing unbounded growth of the shared bucket. Retained prior versions enable rollback by repointing `bm25_current.txt`.

#### Scenario: Retention keeps bucket bounded

- **WHEN** a sync completes and more than N version files exist
- **THEN** version files older than the most recent N SHALL be deleted, and the file referenced by `bm25_current.txt` SHALL never be deleted
