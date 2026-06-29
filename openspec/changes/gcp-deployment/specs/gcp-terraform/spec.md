## ADDED Requirements

### Requirement: Terraform manages all GCP resources

The system SHALL define all GCP resources in Terraform HCL files under the `terraform/` directory. Running `terraform apply` SHALL create or update all required GCP resources without manual Console or gcloud operations.

Resources managed by Terraform SHALL include:
- `google_artifact_registry_repository` (Docker repository named `carbon-assistant`)
- `google_cloud_run_v2_service` (Web App service named `carbon-assistant-web`)
- `google_cloud_run_v2_job` (batch job named `sync-job`)
- `google_storage_bucket` (shared data bucket mounted by both Service and Job)
- `google_secret_manager_secret` (resource definitions for all 9 secrets, values populated separately)
- `google_iam_workload_identity_pool` and `google_iam_workload_identity_pool_provider` (GitHub Actions WIF authentication)
- `google_service_account` and associated IAM bindings (both the GitHub Actions deploy account and the Cloud Run runtime account)

#### Scenario: Fresh environment provisioning

- **WHEN** `terraform init && terraform apply -auto-approve` is run against an empty GCP project
- **THEN** all resources listed above SHALL be created, and `terraform output cloud_run_url` SHALL return the Cloud Run Service HTTPS URL

#### Scenario: Idempotent re-apply

- **WHEN** `terraform apply` is run a second time with no variable changes
- **THEN** Terraform SHALL report `No changes. Infrastructure is up-to-date.`

### Requirement: Terraform remote state stored in GCS

Terraform state SHALL be stored in a GCP Cloud Storage bucket defined in `terraform/backend.tf`, not on the local filesystem.

#### Scenario: CI pipeline accesses shared state

- **WHEN** the GitHub Actions workflow runs `terraform init`
- **THEN** Terraform SHALL retrieve state from the GCS backend bucket and apply changes without state conflicts

#### Scenario: Local developer applies changes

- **WHEN** a developer runs `terraform init && terraform apply` locally with valid GCP credentials
- **THEN** Terraform SHALL use the same remote state as CI, preventing drift between local and CI-applied configurations

### Requirement: Terraform variables control image tag and project configuration

`terraform/variables.tf` SHALL declare variables `project_id`, `region`, and `image_tag`. `terraform/terraform.tfvars.example` SHALL document all required variables without containing sensitive values and SHALL be committed to git. The actual `terraform.tfvars` SHALL be listed in `.gitignore`.

#### Scenario: Image tag updated via variable

- **WHEN** `terraform apply -var="image_tag=abc1234"` is run
- **THEN** both `google_cloud_run_v2_service` and `google_cloud_run_v2_job` SHALL reference the image at `asia-east1-docker.pkg.dev/<project>/carbon-assistant/app:abc1234`

### Requirement: Cloud Run resources configured with Secret Manager references

The `google_cloud_run_v2_service` and `google_cloud_run_v2_job` Terraform resources SHALL reference all required secrets via `env.value_source.secret_key_ref`, not via plaintext environment variable values.

#### Scenario: Secret injection verified at apply time

- **WHEN** `terraform apply` runs and all 9 secrets exist in Secret Manager
- **THEN** both Cloud Run resources SHALL be configured with secret references and deployment SHALL succeed

#### Scenario: Missing secret causes apply failure

- **WHEN** `terraform apply` runs and a required secret does not exist in Secret Manager
- **THEN** Terraform SHALL fail with a resource error before the Cloud Run revision receives traffic

### Requirement: Shared GCS bucket mounted on both Cloud Run resources

Terraform SHALL provision a `google_storage_bucket` for shared data and SHALL configure both `google_cloud_run_v2_service` and `google_cloud_run_v2_job` with a GCS volume mounted at `/mnt/data` referencing that bucket. The Service SHALL also set `max-instances=1`.

#### Scenario: Both resources mount the same bucket

- **WHEN** `terraform apply` completes
- **THEN** the Service and the Job SHALL each have a GCS volume for the same bucket mounted at `/mnt/data`, and the Service SHALL be configured with `max_instance_count = 1`

### Requirement: Cloud Run runtime service account has least-privilege IAM

Terraform SHALL grant the Cloud Run runtime service account `roles/secretmanager.secretAccessor`, object read/write on the shared bucket, and permission to trigger the `sync-job` Job (`roles/run.developer` or an invoker binding on the job). These bindings SHALL be distinct from the GitHub Actions deploy account bindings.

#### Scenario: Runtime account can access secrets, storage, and trigger the Job

- **WHEN** the Service runs after `terraform apply`
- **THEN** the runtime service account SHALL be authorized to read injected secrets, read/write the shared bucket, and invoke `run_job()` against `sync-job`
