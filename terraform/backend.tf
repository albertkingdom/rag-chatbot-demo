# Remote state stored in a GCS bucket (created manually before `terraform init`,
# see tasks 1.2). The backend block cannot use variables, so the bucket is
# supplied at init time: `terraform init -backend-config="bucket=<state-bucket>"`.
# CI reads it from the TF_STATE_BUCKET repository secret.
terraform {
  backend "gcs" {
    prefix = "gcp-deployment/state"
  }
}
