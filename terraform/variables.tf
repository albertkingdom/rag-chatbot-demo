variable "project_id" {
  description = "GCP project ID where all resources are created."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Artifact Registry, and the shared bucket."
  type        = string
  default     = "asia-east1"
}

variable "image_tag" {
  description = "Container image tag (git SHA) to deploy to the Service and Job."
  type        = string
}

variable "github_repository" {
  description = "GitHub repo allowed to authenticate via WIF, in 'owner/repo' form."
  type        = string
}
