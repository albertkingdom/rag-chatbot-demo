## ADDED Requirements

### Requirement: GitHub Actions deploys to Cloud Run on push to release branch

The repository SHALL contain `.github/workflows/deploy.yml` that triggers on every push to any branch matching the pattern `release/**`, builds a Docker image, pushes it to GCP Artifact Registry, then runs `terraform apply` to deploy the new image to both Cloud Run Service and Cloud Run Job. Pushes to `master` SHALL NOT trigger deployment.

#### Scenario: Successful push to release branch triggers full deployment

- **WHEN** a commit is pushed to a branch matching `release/**` (e.g. `release/1.0.0`)
- **THEN** the GitHub Actions workflow SHALL execute the following steps in order: authenticate to GCP via Workload Identity Federation, build Docker image tagged with the git SHA, push image to Artifact Registry, run `terraform init`, run `terraform apply -auto-approve -var="image_tag=<git-sha>"`

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
