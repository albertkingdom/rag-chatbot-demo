"""Spec: metrics-endpoint-exposure — the local monitoring stack's configuration.

Static checks on docker-compose.yml, the Prometheus scrape config, the Grafana
provisioning files and the dashboard JSON. Nothing here starts a container.
"""

import fnmatch
import json
import re
from pathlib import Path, PurePosixPath

import pytest
import yaml
from prometheus_client import REGISTRY

from src import observability  # noqa: F401  (importing it registers the metrics)

REPO_ROOT = Path(__file__).resolve().parents[1]
MONITORING = REPO_ROOT / "monitoring"
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
PROMETHEUS = yaml.safe_load((MONITORING / "prometheus" / "prometheus.yml").read_text())
DASHBOARD = json.loads((MONITORING / "grafana" / "dashboards" / "rag-slo.json").read_text())

# Recording rules the dashboard reads. Tasks 4.1 (availability) and 5.4
# (latency) of the change must define rules with exactly these names.
DASHBOARD_RECORDING_RULES = {
    "slo:chat_availability:ratio_rate5m",
    "slo:chat_availability:target",
    "slo:rag_first_answer_text_good:ratio_rate5m",
    "slo:rag_response_duration_good:ratio_rate5m",
}


def environment(service):
    entries = COMPOSE["services"][service].get("environment", [])
    if isinstance(entries, dict):
        return {key: str(value) for key, value in entries.items()}
    return dict(entry.split("=", 1) for entry in entries)


def mount_target(service, host_path):
    for volume in COMPOSE["services"][service]["volumes"]:
        source, target, *_ = volume.split(":")
        if source == host_path:
            return target
    raise AssertionError(f"{service} does not mount {host_path}")


def dashboard_expressions():
    return [
        target["expr"]
        for panel in DASHBOARD["panels"]
        for target in panel.get("targets", [])
    ]


# --- /metrics is enabled by the local stack only ----------------------------


def test_compose_enables_metrics_for_the_web_service():
    assert environment("web")["METRICS_ENABLED"] == "true"


def test_compose_does_not_enable_metrics_for_the_worker():
    assert "METRICS_ENABLED" not in environment("worker")


def test_terraform_never_sets_the_metrics_flag():
    terraform = REPO_ROOT / "terraform"
    files = [
        path
        for path in terraform.rglob("*")
        if path.is_file() and ".terraform" not in path.relative_to(terraform).parts[:1]
    ]
    assert any(path.suffix == ".tf" for path in files), "terraform sources not found"
    offenders = [str(path.relative_to(REPO_ROOT)) for path in files if b"METRICS_ENABLED" in path.read_bytes()]
    assert offenders == []


# --- Prometheus -------------------------------------------------------------


def test_prometheus_scrapes_the_web_service_metrics_path_over_the_compose_network():
    [job] = PROMETHEUS["scrape_configs"]
    assert job["metrics_path"] == "/metrics"
    # web listens on 80 inside the container: the Dockerfile default PORT, which
    # compose must therefore not override.
    assert job["static_configs"] == [{"targets": ["web:80"]}]
    assert "PORT" not in environment("web")


def test_prometheus_serves_on_host_port_9090_and_grafana_on_3000():
    assert COMPOSE["services"]["prometheus"]["ports"] == ["9090:9090"]
    assert COMPOSE["services"]["grafana"]["ports"] == ["3000:3000"]


@pytest.mark.parametrize("service", ["prometheus", "grafana"])
def test_monitoring_images_are_pinned_to_an_explicit_version(service):
    image = COMPOSE["services"][service]["image"]
    name, _, tag = image.partition(":")
    assert name and re.fullmatch(r"v?\d+\.\d+\.\d+", tag), f"{image} is not pinned to a version"


def test_prometheus_never_loads_the_promtool_test_file_as_rules():
    # slo_test.yml shares the rules directory and is not a rule file: loading it
    # makes Prometheus reject its configuration.
    rules_dir = mount_target("prometheus", "./monitoring/prometheus/rules")
    for pattern in PROMETHEUS["rule_files"]:
        path = PurePosixPath(pattern)
        if str(path.parent) == rules_dir:
            assert not fnmatch.fnmatch("slo_test.yml", path.name), f"{pattern} matches slo_test.yml"


def test_rule_files_point_at_the_mounted_rules_directory():
    rules_dir = mount_target("prometheus", "./monitoring/prometheus/rules")
    for pattern in PROMETHEUS["rule_files"]:
        assert str(PurePosixPath(pattern).parent) == rules_dir
        assert (MONITORING / "prometheus" / "rules" / PurePosixPath(pattern).name).is_file()


# --- Grafana provisioning ---------------------------------------------------


def test_datasource_is_provisioned_with_the_uid_the_dashboard_uses():
    config = yaml.safe_load((MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml").read_text())
    [datasource] = config["datasources"]
    assert datasource["type"] == "prometheus"
    assert datasource["url"] == "http://prometheus:9090"
    for panel in DASHBOARD["panels"]:
        if panel["type"] != "text":
            assert panel["datasource"] == {"type": "prometheus", "uid": datasource["uid"]}
            assert all(t["datasource"]["uid"] == datasource["uid"] for t in panel["targets"])


def test_dashboard_provider_reads_the_directory_compose_mounts():
    config = yaml.safe_load((MONITORING / "grafana" / "provisioning" / "dashboards" / "dashboards.yml").read_text())
    [provider] = config["providers"]
    assert provider["type"] == "file"
    assert provider["options"]["path"] == mount_target("grafana", "./monitoring/grafana/dashboards")


def test_grafana_provisioning_directory_is_mounted():
    assert mount_target("grafana", "./monitoring/grafana/provisioning") == "/etc/grafana/provisioning"


# --- dashboard --------------------------------------------------------------


def test_dashboard_can_be_provisioned_without_a_database_id():
    assert DASHBOARD["id"] is None
    assert DASHBOARD["uid"] and DASHBOARD["title"]


def test_dashboard_panels_have_unique_ids_titles_and_fit_the_24_column_grid():
    panels = DASHBOARD["panels"]
    assert len({p["id"] for p in panels}) == len(panels)
    assert len({p["title"] for p in panels}) == len(panels)
    for panel in panels:
        grid = panel["gridPos"]
        assert grid["x"] >= 0 and grid["x"] + grid["w"] <= 24 and grid["w"] > 0 and grid["h"] > 0


def test_dashboard_has_every_panel_the_design_lists():
    exprs = "\n".join(dashboard_expressions())
    expected = {
        "request rate by response source": "sum by (response_source) (rate(chat_requests_total",
        "availability": 'chat_requests_total{outcome="success"}',
        "first answer time quantiles": "chat_first_answer_text_seconds_bucket",
        "response duration quantiles": "chat_response_duration_seconds_bucket",
        "SLO attainment": "slo:chat_availability:ratio_rate5m",
        "error budget remaining": "[28d]",
        "low confidence ratio": 'rag_retrieval_total{low_confidence="true"}',
        "retrieval retry rate": "rag_retrieval_retries_total",
        "rerank P95": "histogram_quantile(0.95, sum by (le) (rate(rag_rerank_duration_seconds_bucket",
        "rerank degradation": 'rag_rerank_duration_seconds_count{outcome!="ok"}',
    }
    missing = [name for name, fragment in expected.items() if fragment not in exprs]
    assert missing == []


def test_latency_panels_cover_the_rag_path_only():
    for panel in DASHBOARD["panels"]:
        if "rag 首段回答" in panel["title"] or "rag 完整回應" in panel["title"]:
            assert all('response_source="rag"' in t["expr"] for t in panel["targets"])


def test_error_budget_panel_explains_what_it_shows_with_too_little_data():
    panel = next(p for p in DASHBOARD["panels"] if "error budget" in p["title"])
    assert "since measuring began" in panel["description"]
    notes = next(p for p in DASHBOARD["panels"] if p["type"] == "text")
    assert "less than 28 days" in notes["options"]["content"]


def test_dashboard_reads_the_agreed_recording_rule_names():
    used = set(re.findall(r"slo:[a-z0-9_:]+", "\n".join(dashboard_expressions())))
    assert used == DASHBOARD_RECORDING_RULES


def test_dashboard_only_queries_metrics_the_backend_defines():
    defined = {sample.name for family in REGISTRY.collect() for sample in family.samples}
    referenced = set()
    for expr in dashboard_expressions():
        referenced |= set(re.findall(r"(?<![:\w])(?:chat|rag)_[a-z_]+", expr))
    assert referenced, "no backend metrics referenced"
    assert referenced <= defined, sorted(referenced - defined)


def test_dashboard_does_not_repeat_latency_thresholds():
    # SLO thresholds live in the Prometheus rules; panels read recording rules.
    for expr in dashboard_expressions():
        assert not re.search(r'le="[\d.]+"', expr)


def test_rerank_p95_panel_excludes_degraded_attempts():
    # A timed-out attempt is cut off near the rerank timeout; counting it would
    # report the timeout, not how long a working rerank takes.
    quantile_exprs = [
        expr for expr in dashboard_expressions()
        if "histogram_quantile" in expr and "rag_rerank_duration_seconds_bucket" in expr
    ]
    assert quantile_exprs
    assert all('rag_rerank_duration_seconds_bucket{outcome="ok"}' in expr for expr in quantile_exprs)
