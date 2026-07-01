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

variable "allowed_invoker_members" {
  description = <<EOT
List of principals allowed to invoke the Cloud Run Service (roles/run.invoker).
Default is empty — the Service is fully closed. Add members (e.g.
"user:demo@example.com", "group:team@example.com", or "allUsers" to revert to
public access relying on the app-layer API key) to grant access.
EOT
  type        = list(string)
  default     = []
}

variable "rate_limit_rpm" {
  description = "Per-API-key request rate limit (requests per minute) injected into the Service."
  type        = number
  default     = 60
}

variable "session_ttl_seconds" {
  description = "TTL in seconds for Redis-backed session tokens issued by /login."
  type        = number
  default     = 86400
}
