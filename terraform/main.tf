terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # The 9 secrets injected into both Cloud Run resources from Secret Manager.
  secret_names = [
    "OPENAI_API_KEY",
    "PINECONE_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "MONGODB_URL",
    "REDIS_URL",
  ]

  # Plain (non-secret) environment variables shared by Service and Job.
  shared_data_dir = "/mnt/data"
  image_url       = "${var.region}-docker.pkg.dev/${var.project_id}/carbon-assistant/carbon-assistant:${var.image_tag}"
}

# ---------------------------------------------------------------------------
# Artifact Registry (Docker images)
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "carbon-assistant"
  format        = "DOCKER"
  description   = "Docker images for the Carbon Assistant app"
}

# ---------------------------------------------------------------------------
# Shared storage: GCS bucket mounted on both the Service and the Job
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "shared" {
  name                        = "${var.project_id}-carbon-assistant-data"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
}

# ---------------------------------------------------------------------------
# Secret Manager: resource shells only (values added manually, task 4.1)
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(local.secret_names)
  secret_id = each.value

  replication {
    auto {}
  }
}

# ---------------------------------------------------------------------------
# Runtime service account (used by the Service and the Job)
# ---------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "carbon-assistant-runtime"
  display_name = "Carbon Assistant Cloud Run runtime SA"
}

# Read secrets (container fails to start without this).
resource "google_project_iam_member" "runtime_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Trigger the sync Job from the Service (roles/run.developer includes run.jobs.run).
resource "google_project_iam_member" "runtime_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Read/write objects in the shared bucket (uploads + BM25 version/pointer files).
resource "google_storage_bucket_iam_member" "runtime_bucket_objects" {
  bucket = google_storage_bucket.shared.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# ---------------------------------------------------------------------------
# Cloud Run Service (web app)
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "web" {
  name     = "carbon-assistant-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    # Shared GCS volume (2nd-gen FUSE) for uploads + BM25 index.
    volumes {
      name = "data"
      gcs {
        bucket    = google_storage_bucket.shared.name
        read_only = false
      }
    }

    containers {
      image = local.image_url
      # No `command`: the Service uses the Dockerfile CMD (uvicorn web server).

      resources {
        limits = {
          cpu    = "4"
          memory = "2Gi"
        }
      }

      volume_mounts {
        name       = "data"
        mount_path = local.shared_data_dir
      }

      env {
        name  = "JOB_RUNNER"
        value = "gcp"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "SYNC_JOB_NAME"
        value = "sync-job"
      }
      env {
        name  = "DATA_SOURCE_DIR"
        value = "${local.shared_data_dir}/uploaded_files"
      }
      env {
        name  = "BM25_INDEX_DIR"
        value = "${local.shared_data_dir}/models"
      }
      env {
        name  = "LANGCHAIN_TRACING_V2"
        value = "true"
      }
      # Match torch/BLAS thread count to allocated vCPUs (resources.limits.cpu).
      # Without this, torch defaults to 1 thread and the extra cores stay idle,
      # so the BGE reranker would see no speedup from the CPU bump.
      env {
        name  = "OMP_NUM_THREADS"
        value = "4"
      }

      dynamic "env" {
        for_each = toset(local.secret_names)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret.secrets]
}

# Allow unauthenticated public access to the web app.
resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.web.location
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Cloud Run Job (sync_vector_store)
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_job" "sync" {
  name     = "sync-job"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "3600s"

      volumes {
        name = "data"
        gcs {
          bucket    = google_storage_bucket.shared.name
          read_only = false
        }
      }

      containers {
        image = local.image_url
        # Override the entrypoint to run sync once and exit (vs the web server).
        command = ["python", "-m", "src.build_vector_store"]

        volume_mounts {
          name       = "data"
          mount_path = local.shared_data_dir
        }

        env {
          name  = "DATA_SOURCE_DIR"
          value = "${local.shared_data_dir}/uploaded_files"
        }
        env {
          name  = "BM25_INDEX_DIR"
          value = "${local.shared_data_dir}/models"
        }
        env {
          name  = "LANGCHAIN_TRACING_V2"
          value = "true"
        }

        dynamic "env" {
          for_each = toset(local.secret_names)
          content {
            name = env.value
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secrets[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret.secrets]
}

# ---------------------------------------------------------------------------
# Workload Identity Federation for GitHub Actions
# ---------------------------------------------------------------------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions-provider"
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Only tokens from the configured repository may use this provider.
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Deploy service account used by GitHub Actions (distinct from the runtime SA).
resource "google_service_account" "github_actions" {
  account_id   = "github-actions-deployer"
  display_name = "GitHub Actions deployer SA"
}

# NOTE: run.admin + artifactregistry.writer cover deploying/updating Cloud Run
# and pushing images (per design). A full `terraform apply` from CI that also
# creates buckets, secrets, and IAM needs broader project roles — grant those
# per your security posture (see the apply summary / follow-up note).
resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "deployer_artifactregistry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

# Allow the GitHub repo's WIF principal to impersonate the deploy SA.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
