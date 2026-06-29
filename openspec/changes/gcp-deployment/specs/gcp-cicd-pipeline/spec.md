## ADDED Requirements

### Requirement: GitHub Actions deploys to Cloud Run on push to release branch

The repository SHALL contain `.github/workflows/deploy.yml` that triggers on every push to any branch matching the pattern `release/**`, builds a Docker image, pushes it to GCP Artifact Registry, then runs a **scoped** `terraform apply` that targets ONLY the Cloud Run Service and Cloud Run Job (the app layer) to deploy the new image. Pushes to `master` SHALL NOT trigger deployment.

The CI `terraform apply` SHALL use `-target=google_cloud_run_v2_service.web -target=google_cloud_run_v2_job.sync` so that the pipeline only rolls a new image and never creates or modifies foundational infrastructure (service accounts, IAM bindings, Secret Manager, Workload Identity Federation, buckets, Artifact Registry repo). Foundational infrastructure SHALL be applied by a human operator running `terraform apply` locally with owner-level credentials.

#### Scenario: Successful push to release branch triggers scoped app deployment

- **WHEN** a commit is pushed to a branch matching `release/**` (e.g. `release/1.0.0`)
- **THEN** the GitHub Actions workflow SHALL execute the following steps in order: authenticate to GCP via Workload Identity Federation, build Docker image tagged with the git SHA, push image to Artifact Registry, run `terraform init`, run `terraform apply -auto-approve -target=google_cloud_run_v2_service.web -target=google_cloud_run_v2_job.sync -var="image_tag=<git-sha>"`
- **AND** the apply SHALL NOT add, change, or destroy any foundational resource (service accounts, IAM members, secrets, WIF pool/provider, buckets, Artifact Registry repo)

#### Scenario: Push to master does not trigger deployment

- **WHEN** a commit is pushed to the `master` branch
- **THEN** the GitHub Actions workflow SHALL NOT be triggered and no deployment SHALL occur

#### Scenario: Workflow fails on build error

- **WHEN** `docker build` fails during the workflow
- **THEN** the workflow SHALL exit with a non-zero status and `terraform apply` SHALL NOT run

#### Scenario: Workflow fails on terraform apply error

- **WHEN** `terraform apply` fails during the workflow
- **THEN** the workflow SHALL exit with a non-zero status and the previous Cloud Run revision SHALL continue serving traffic

### Requirement: Docker image tagged with git SHA

Each Docker image pushed to Artifact Registry SHALL be tagged with the full git commit SHA to enable precise rollback.

#### Scenario: Image tag corresponds to deployed commit

- **WHEN** the workflow completes and the Cloud Run Service is serving traffic
- **THEN** the active revision's image tag SHALL match the git SHA of the triggering commit

##### Example: image tag format

- **GIVEN** git SHA `abc1234def5678`
- **THEN** image tag SHALL be `asia-east1-docker.pkg.dev/<project>/carbon-assistant/app:abc1234def5678`

### Requirement: GCP authentication uses Workload Identity Federation

The GitHub Actions workflow SHALL authenticate to GCP using Workload Identity Federation (WIF) with no long-lived service account JSON keys stored in GitHub Secrets.

#### Scenario: Workflow authenticates without service account key file

- **WHEN** the workflow runs the `google-github-actions/auth` step
- **THEN** authentication SHALL succeed using the WIF provider and service account impersonation, with no JSON key file present in the runner environment

#### Scenario: Required GitHub Secrets are defined

- **WHEN** the workflow is triggered
- **THEN** GitHub repository secrets SHALL contain `GCP_PROJECT_ID`, `WIF_PROVIDER`, and `WIF_SERVICE_ACCOUNT`; no API keys or service account JSON SHALL be stored in GitHub Secrets

### Requirement: CI deployer service account holds least-privilege roles only

The GitHub Actions deployer service account (`github-actions-deployer@`) SHALL hold only the permissions required to roll a new Cloud Run image, NOT permissions to manage foundational infrastructure. A compromise of CI SHALL NOT allow IAM, WIF, or secret modification / privilege escalation.

The deployer SA SHALL be granted:
- `roles/run.admin` (manage the Cloud Run Service and Job)
- `roles/artifactregistry.writer` (push images)
- `roles/iam.serviceAccountUser` on the runtime SA (act as it when deploying the Service/Job)
- `roles/secretmanager.viewer` (read-only refresh of the secret resources referenced by the targeted Cloud Run resources — metadata only, NOT secret values)
- `roles/storage.objectAdmin` on the tfstate bucket (read/write/lock Terraform state)
- `roles/storage.legacyBucketReader` on the shared data bucket (read-only refresh of the bucket resource referenced as a Cloud Run volume)

The deployer SA SHALL NOT hold `roles/owner`, `roles/editor`, `roles/secretmanager.admin`, `roles/iam.serviceAccountAdmin`, `roles/resourcemanager.projectIamAdmin`, or any role that can create/modify service accounts, IAM bindings, secrets, or WIF configuration.

#### Scenario: Deployer can roll an image but cannot escalate

- **WHEN** the CI pipeline runs the scoped `terraform apply`
- **THEN** the deployer SA SHALL successfully update the Cloud Run Service and Job image
- **AND** any attempt by the deployer SA to create/modify a service account, IAM binding, secret value, or WIF resource SHALL be denied by IAM

#### Scenario: Required GCP APIs are enabled

- **WHEN** the CI pipeline runs `terraform apply` (which refreshes the runtime SA and other dependencies)
- **THEN** the project SHALL have `iam.googleapis.com` and `iamcredentials.googleapis.com` enabled (in addition to run / artifactregistry / secretmanager / storage), otherwise WIF impersonation and SA refresh fail
