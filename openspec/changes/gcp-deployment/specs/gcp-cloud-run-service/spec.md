## ADDED Requirements

### Requirement: Web App deployed to Cloud Run Service

The system SHALL deploy the FastAPI + Gradio application as a Cloud Run Service in the `asia-east1` region with a minimum of 2GB memory and 1 vCPU.

#### Scenario: Public HTTPS URL available after deployment

- **WHEN** deployment completes successfully
- **THEN** the service SHALL be accessible at a GCP-generated HTTPS URL (`https://<service>-<hash>-de.a.run.app`) without authentication

#### Scenario: Unauthenticated access allowed

- **WHEN** any user sends an HTTP request to the Cloud Run URL
- **THEN** the service SHALL respond without requiring Google identity verification (IAM `roles/run.invoker` granted to `allUsers`)

### Requirement: Service listens on the Cloud Run injected $PORT

The web server SHALL bind to the port provided by the Cloud Run `PORT` environment variable, falling back to `80` when `PORT` is unset. The `Dockerfile` `CMD` SHALL use a shell form so `${PORT:-80}` is expanded at runtime. The `google_cloud_run_v2_service` resource SHALL NOT set an explicit `container_port`, relying on the Cloud Run default (8080). The same image SHALL therefore serve correctly both on Cloud Run (where `PORT=8080`) and in local docker-compose (where `PORT` is unset).

#### Scenario: Cloud Run routes traffic to the injected port

- **WHEN** Cloud Run starts the container and injects `PORT=8080`
- **THEN** uvicorn SHALL listen on `8080` and respond to the platform health check and incoming traffic

#### Scenario: Local run falls back to port 80

- **WHEN** the container runs without `PORT` set (local docker-compose)
- **THEN** uvicorn SHALL listen on `80`, matching the prior behavior

### Requirement: BGE Reranker model pre-downloaded in Docker image

The Dockerfile SHALL download the `BAAI/bge-reranker-v2-m3` model during `docker build` using `huggingface_hub.snapshot_download`. The download `cache_dir` and the runtime `cache_folder` passed to `HuggingFaceCrossEncoder` SHALL both resolve from a single source — the `MODEL_CACHE_DIR` environment variable (default `/app/models`) — so the pre-download path cannot drift from the load path. `requirements.txt` SHALL pin `sentence-transformers` and `huggingface_hub` to versions whose `CrossEncoder` caching uses the same HF hub layout (`models--BAAI--bge-reranker-v2-m3/...`) as `snapshot_download`.

The runtime SHALL set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` so that, when the model is present in the cache, no network request (including revision HEAD checks) is made at cold start.

#### Scenario: Pre-download and load paths share one source

- **WHEN** the image is built and a container later loads the reranker
- **THEN** `snapshot_download` (build) and `HuggingFaceCrossEncoder` (runtime) SHALL both use the directory named by `MODEL_CACHE_DIR`, and the model files written at build time SHALL be the same files read at runtime

#### Scenario: Cold start performs no network access

- **WHEN** Cloud Run starts a new container instance with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`
- **THEN** `get_reranker_model()` SHALL load the model from the cache directory with no network request to HuggingFace (no download and no revision HEAD check)

#### Scenario: Reranker initializes within acceptable time

- **WHEN** the first request arrives at a cold container
- **THEN** the reranker model SHALL finish loading within 30 seconds

### Requirement: API secrets injected via GCP Secret Manager

The Cloud Run Service SHALL source all API keys from Secret Manager secrets mounted as environment variables, with no plaintext credentials in the container image or environment configuration files.

#### Scenario: Service starts with all required secrets present

- **WHEN** the Cloud Run Service starts and all 9 secrets (`OPENAI_API_KEY`, `PINECONE_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `LANGCHAIN_API_KEY`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_PROJECT`, `MONGODB_URL`, `REDIS_URL`) are present in Secret Manager
- **THEN** `os.environ` inside the container SHALL contain each secret value and the application SHALL start without error

#### Scenario: Redis available in GCP mode for semantic cache

- **WHEN** the Service runs with `JOB_RUNNER=gcp`
- **THEN** `REDIS_URL` SHALL be present and a Redis connection (`conn`) SHALL be initialized for the `PromptCacheService` semantic cache, independent of any RQ queue initialization

#### Scenario: Missing secret causes deployment failure

- **WHEN** a required secret is absent from Secret Manager during deployment
- **THEN** the deployment SHALL fail with a permission or secret-not-found error before the new revision receives traffic

### Requirement: Scale-to-zero with single-instance cap

The Cloud Run Service SHALL have `min-instances` set to 0 and `max-instances` set to 1. Scale-to-zero avoids idle cost; the single-instance cap prevents Gradio session-routing breakage and in-memory BM25 index inconsistency across instances.

#### Scenario: No idle cost when service has no traffic

- **WHEN** no HTTP requests have been received for the service cooldown period
- **THEN** Cloud Run SHALL terminate all instances and billing SHALL stop until the next request arrives

#### Scenario: At most one instance under load

- **WHEN** concurrent requests arrive at the service
- **THEN** Cloud Run SHALL NOT start more than one container instance, keeping a single in-memory BM25 index and consistent Gradio session routing

### Requirement: Shared GCS storage mounted as a volume

The Cloud Run Service SHALL mount a shared GCS bucket as a FUSE volume at `/mnt/data`. The uploaded-files directory and the BM25 index path SHALL resolve to subdirectories under this mount in GCP mode, sourced from environment variables so that local docker-compose continues to use relative paths.

#### Scenario: Service reads BM25 index written by the Job

- **WHEN** the Cloud Run Job has written a BM25 version file and pointer to the shared bucket
- **THEN** the Service SHALL be able to read those files from `/mnt/data` (the Service and Job mount the same bucket)

#### Scenario: Uploaded file is visible to the Job

- **WHEN** a user uploads a file via the Service and the file is written under `/mnt/data`
- **THEN** the subsequently triggered Cloud Run Job SHALL read the same file from its own `/mnt/data` mount

### Requirement: Service auto-loads the latest BM25 index after sync

The Service SHALL serve the most recent BM25 index after a sync completes, without requiring a container restart. `get_bm25_index()` SHALL consult the pointer file and reload the in-memory index when the pointer version differs from the loaded version.

#### Scenario: New upload becomes searchable without restart

- **WHEN** a sync completes and updates the BM25 pointer file to a new version
- **THEN** the next query SHALL detect the version change and reload the index, so results reflect the newly uploaded documents without restarting the Service
