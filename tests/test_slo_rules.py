"""Spec: slo-burn-rate-rules, continuous-integration — the SLO rule files and the CI job.

promtool runs the real rule tests (in CI, and locally with the same pinned
image); nothing here starts a container. These checks keep the rule file, its
promtool test file, the dashboard, docker-compose.yml and the CI workflow
consistent with one another.
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = REPO_ROOT / "monitoring" / "prometheus" / "rules"
RULES = yaml.safe_load((RULES_DIR / "slo.yml").read_text())
RULE_TESTS = yaml.safe_load((RULES_DIR / "slo_test.yml").read_text())
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
CI_TEXT = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
CI = yaml.safe_load(CI_TEXT)
DASHBOARD_TEXT = (REPO_ROOT / "monitoring" / "grafana" / "dashboards" / "rag-slo.json").read_text()


def all_rules():
    return [rule for group in RULES["groups"] for rule in group["rules"]]


def recording_rules():
    return {rule["record"]: rule for rule in all_rules() if "record" in rule}


def alert_rules():
    return {rule["alert"]: rule for rule in all_rules() if "alert" in rule}


def squash(expr):
    return " ".join(expr.split())


# --- rules (task 4.1) ---------------------------------------------------------


def test_availability_recording_rules_exist_for_every_burn_rate_window():
    assert set(recording_rules()) >= {
        "slo:chat_availability:target",
        "slo:chat_availability:ratio_rate5m",
        "slo:chat_availability:error_ratio_rate5m",
        "slo:chat_availability:error_ratio_rate30m",
        "slo:chat_availability:error_ratio_rate1h",
        "slo:chat_availability:error_ratio_rate6h",
    }


def test_availability_target_is_99_5_percent():
    assert recording_rules()["slo:chat_availability:target"]["expr"] == "vector(0.995)"


def test_each_error_ratio_rule_uses_the_window_in_its_name():
    for window in ("5m", "30m", "1h", "6h"):
        expr = recording_rules()[f"slo:chat_availability:error_ratio_rate{window}"]["expr"]
        assert set(re.findall(r"\[(\w+)\]", expr)) == {window}


def test_dashboard_availability_rules_are_defined():
    # The latency rules the dashboard also reads arrive with task 5.4.
    for name in ("slo:chat_availability:ratio_rate5m", "slo:chat_availability:target"):
        assert name in DASHBOARD_TEXT
        assert name in recording_rules()


def test_availability_rules_cover_every_response_source_and_exclude_cancelled():
    for name, rule in recording_rules().items():
        if "chat_requests_total" not in rule["expr"]:
            continue
        assert "response_source" not in rule["expr"], f"{name} filters on response_source"
        outcomes = re.findall(r'outcome=~?"([^"]+)"', rule["expr"])
        assert outcomes and set(outcomes) <= {"success", "error", "success|error"}, name
        assert "cancelled" not in rule["expr"]


def test_ratio_rules_drop_windows_without_traffic_instead_of_producing_nan():
    for name, rule in recording_rules().items():
        if name.startswith("slo:chat_availability:") and "/" in rule["expr"]:
            assert re.search(r"\)\s*>\s*0\)\s*$", squash(rule["expr"])), f"{name} has no empty-denominator guard"


def test_there_is_one_fast_and_one_slow_burn_alert_with_severity_and_a_chinese_summary():
    alerts = alert_rules()

    assert set(alerts) == {"ChatAvailabilityFastBurn", "ChatAvailabilitySlowBurn"}
    for alert in alerts.values():
        assert alert["labels"]["severity"]
        assert re.search(r"[一-鿿]", alert["annotations"]["summary"])


def test_fast_burn_needs_1h_and_5m_above_14_4_times_the_budget():
    expr = squash(alert_rules()["ChatAvailabilityFastBurn"]["expr"])

    assert "error_ratio_rate1h >" in expr and "error_ratio_rate5m >" in expr
    assert " and " in expr
    assert expr.count("14.4 * (1 - slo:chat_availability:target)") == 2


def test_slow_burn_needs_6h_and_30m_above_6_times_the_budget():
    expr = squash(alert_rules()["ChatAvailabilitySlowBurn"]["expr"])

    assert "error_ratio_rate6h >" in expr and "error_ratio_rate30m >" in expr
    assert " and " in expr
    assert expr.count("(6 * (1 - slo:chat_availability:target))") == 2


# --- promtool test file (task 4.1) ----------------------------------------------


def test_promtool_test_file_loads_the_rules_next_to_it():
    assert RULE_TESTS["rule_files"] == ["slo.yml"]
    assert (RULES_DIR / "slo.yml").is_file()


def test_every_alert_has_a_firing_and_a_quiet_promtool_case():
    cases = [case for group in RULE_TESTS["tests"] for case in group.get("alert_rule_test", [])]
    for name in alert_rules():
        outcomes = [bool(case["exp_alerts"]) for case in cases if case["alertname"] == name]
        assert True in outcomes, f"{name} is never expected to fire"
        assert False in outcomes, f"{name} is never expected to stay quiet"


def test_promtool_cases_cover_unknown_source_cancelled_and_no_traffic():
    names = " ".join(group["name"] for group in RULE_TESTS["tests"])

    assert "unknown" in names
    assert "cancelled" in names
    assert "no traffic" in names


# --- CI job (task 4.2) ----------------------------------------------------------


def ci_job():
    return CI["jobs"]["prometheus"]


def test_ci_has_a_prometheus_rules_job():
    assert ci_job()["name"] == "Prometheus rules"


def test_ci_uses_the_same_prometheus_image_tag_as_the_compose_stack():
    assert ci_job()["env"]["PROMETHEUS_IMAGE"] == COMPOSE["services"]["prometheus"]["image"]


def test_ci_names_the_prometheus_image_in_one_place_only():
    assert len(re.findall(r"prom/prometheus\S*", CI_TEXT)) == 1


def test_ci_runs_promtool_check_config_and_test_rules_in_that_image():
    runs = [step["run"] for step in ci_job()["steps"] if "run" in step]
    commands = [squash(run) for run in runs]

    assert any(
        '--entrypoint promtool "$PROMETHEUS_IMAGE" check config /etc/prometheus/prometheus.yml' in c
        for c in commands
    )
    assert any(
        '--entrypoint promtool "$PROMETHEUS_IMAGE" test rules /etc/prometheus/rules/slo_test.yml' in c
        for c in commands
    )
    assert all('-v "$PWD/monitoring/prometheus:/etc/prometheus' in c for c in commands)


# --- latency rules (task 5.4) -------------------------------------------------

LATENCY_RULES = {
    "slo:rag_first_answer_text_good:ratio_rate5m": "chat_first_answer_text_seconds",
    "slo:rag_response_duration_good:ratio_rate5m": "chat_response_duration_seconds",
}


def test_dashboard_latency_rules_are_defined_on_the_rag_path():
    for name, metric in LATENCY_RULES.items():
        assert name in DASHBOARD_TEXT
        expr = squash(recording_rules()[name]["expr"])
        assert f'{metric}_bucket{{response_source="rag",' in expr
        assert f'{metric}_count{{response_source="rag"}}' in expr
        assert "> 0" in expr  # no traffic gives no sample, not NaN


def test_slo_latency_thresholds_are_histogram_bucket_boundaries():
    from src.observability import LATENCY_BUCKETS

    thresholds = [
        float(value)
        for rule in all_rules()
        for value in re.findall(r'le="([^"]+)"', rule.get("expr", ""))
    ]
    assert thresholds, "the latency rules must reference an le bucket"
    assert all(t in [float(b) for b in LATENCY_BUCKETS] for t in thresholds)


def test_latency_rules_have_promtool_cases():
    exprs = " ".join(
        case.get("expr", "")
        for test in RULE_TESTS["tests"]
        for case in test.get("promql_expr_test", [])
    )
    for name in LATENCY_RULES:
        assert name in exprs
