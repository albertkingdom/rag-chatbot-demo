# Remote state stored in a GCS bucket (created manually before `terraform init`,
# see tasks 1.2). The backend block cannot use variables, so set the bucket
# either by editing it here or via `terraform init -backend-config="bucket=<name>"`.
terraform {
  backend "gcs" {
    bucket = "carbon-rag-assistant-prod-2026-tfstate"
    prefix = "gcp-deployment/state"
  }
}
