output "cloud_run_url" {
  description = "Public HTTPS URL of the Cloud Run web service."
  value       = google_cloud_run_v2_service.web.uri
}

output "github_actions_service_account" {
  description = "Deploy SA email to configure as WIF_SERVICE_ACCOUNT in GitHub."
  value       = google_service_account.github_actions.email
}

output "workload_identity_provider" {
  description = "Full WIF provider resource name to set as WIF_PROVIDER in GitHub."
  value       = google_iam_workload_identity_pool_provider.github.name
}
