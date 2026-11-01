import json

import pytest

from quota_fit_planner.model import (
    QUOTA_KEYS,
    FleetError,
    fleet_from_dict,
    load_fleet,
)

BASE = {
    "name": "tiny",
    "hours_per_month": 730,
    "machine_types": [
        {"name": "small", "vcpu": 2, "memory_gb": 8, "hourly_usd": 0.10},
        {"name": "gpu", "vcpu": 8, "memory_gb": 32, "gpus": 2, "hourly_usd": 2.00},
    ],
    "regions": [
        {"name": "cheap", "price_index": 0.5,
         "quotas": {"vcpu": 16, "memory_gb": 64, "gpus": 0, "public_ips": 8}},
        {"name": "dear", "price_index": 1.0,
         "quotas": {"vcpu": 16, "memory_gb": 64, "gpus": 4, "public_ips": 8}},
    ],
    "workloads": [
        {"name": "web", "machine": "small", "count": 4},
        {"name": "ml", "machine": "gpu", "count": 1},
    ],
}


def test_quota_keys_are_the_four_dimensions():
    assert QUOTA_KEYS == ("vcpu", "memory_gb", "gpus", "public_ips")


def test_parses_machines_regions_workloads():
    fleet = fleet_from_dict(BASE)
    assert fleet.name == "tiny"
    assert fleet.machine_names == ("small", "gpu")
    assert [region.name for region in fleet.regions] == ["cheap", "dear"]
    assert [workload.name for workload in fleet.workloads] == ["web", "ml"]
    assert fleet.hours_per_month == 730


def test_unit_cost_applies_the_price_index():
    fleet = fleet_from_dict(BASE)
    assert fleet.unit_cost("small", "cheap") == pytest.approx(0.05)
    assert fleet.unit_cost("small", "dear") == pytest.approx(0.10)


def test_demand_multiplies_by_count():
    fleet = fleet_from_dict(BASE)
    demand = fleet.demand("web")
    assert demand["vcpu"] == 8
    assert demand["memory_gb"] == 32
    assert demand["public_ips"] == 4
    assert demand["gpus"] == 0


def test_candidates_are_cheapest_first():
    fleet = fleet_from_dict(BASE)
    assert fleet.candidates("web") == ("cheap", "dear")
    assert fleet.candidates("ml") == ("cheap", "dear")


def test_allowed_for_restricts_regions():
    payload = json.loads(json.dumps(BASE))
    payload["regions"][0]["allowed_for"] = ["web"]
    fleet = fleet_from_dict(payload)
    assert fleet.candidates("ml") == ("dear",)


def test_pinned_workload_has_one_candidate():
    payload = json.loads(json.dumps(BASE))
    payload["workloads"][1]["pinned_region"] = "dear"
    fleet = fleet_from_dict(payload)
    assert fleet.candidates("ml") == ("dear",)


def test_rejects_missing_machine_types():
    with pytest.raises(FleetError, match="machine_types"):
        fleet_from_dict({"regions": BASE["regions"], "workloads": BASE["workloads"]})


def test_rejects_machine_without_capacity():
    payload = json.loads(json.dumps(BASE))
    payload["machine_types"][0]["vcpu"] = 0
    with pytest.raises(FleetError, match="positive vcpu"):
        fleet_from_dict(payload)


def test_rejects_negative_machine_price():
    payload = json.loads(json.dumps(BASE))
    payload["machine_types"][0]["hourly_usd"] = -1
    with pytest.raises(FleetError, match="negative price"):
        fleet_from_dict(payload)


def test_rejects_duplicate_machine():
    payload = json.loads(json.dumps(BASE))
    payload["machine_types"].append({"name": "small", "vcpu": 1, "memory_gb": 1})
    with pytest.raises(FleetError, match="duplicate machine"):
        fleet_from_dict(payload)


def test_rejects_region_without_quotas():
    payload = json.loads(json.dumps(BASE))
    del payload["regions"][0]["quotas"]
    with pytest.raises(FleetError, match="quotas object"):
        fleet_from_dict(payload)


def test_rejects_unknown_quota_key():
    payload = json.loads(json.dumps(BASE))
    payload["regions"][0]["quotas"]["disk_gb"] = 100
    with pytest.raises(FleetError, match="unknown quota keys"):
        fleet_from_dict(payload)


def test_rejects_negative_quota():
    payload = json.loads(json.dumps(BASE))
    payload["regions"][0]["quotas"]["vcpu"] = -1
    with pytest.raises(FleetError, match="negative vcpu"):
        fleet_from_dict(payload)


def test_rejects_non_positive_price_index():
    payload = json.loads(json.dumps(BASE))
    payload["regions"][0]["price_index"] = 0
    with pytest.raises(FleetError, match="non-positive price_index"):
        fleet_from_dict(payload)


def test_rejects_duplicate_region():
    payload = json.loads(json.dumps(BASE))
    payload["regions"].append(payload["regions"][0])
    with pytest.raises(FleetError, match="duplicate region"):
        fleet_from_dict(payload)


def test_rejects_unknown_machine_on_workload():
    payload = json.loads(json.dumps(BASE))
    payload["workloads"][0]["machine"] = "huge"
    with pytest.raises(FleetError, match="unknown machine"):
        fleet_from_dict(payload)


def test_rejects_zero_count():
    payload = json.loads(json.dumps(BASE))
    payload["workloads"][0]["count"] = 0
    with pytest.raises(FleetError, match="positive count"):
        fleet_from_dict(payload)


def test_rejects_unknown_pinned_region():
    payload = json.loads(json.dumps(BASE))
    payload["workloads"][0]["pinned_region"] = "mars"
    with pytest.raises(FleetError, match="unknown region"):
        fleet_from_dict(payload)


def test_rejects_duplicate_workload():
    payload = json.loads(json.dumps(BASE))
    payload["workloads"].append({"name": "web", "machine": "small", "count": 1})
    with pytest.raises(FleetError, match="duplicate workload"):
        fleet_from_dict(payload)


def test_rejects_bad_hours():
    payload = json.loads(json.dumps(BASE))
    payload["hours_per_month"] = 0
    with pytest.raises(FleetError, match="hours_per_month"):
        fleet_from_dict(payload)


def test_load_fleet_round_trip(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    assert load_fleet(path).name == "tiny"


def test_load_fleet_missing(tmp_path):
    with pytest.raises(FleetError, match="file not found"):
        load_fleet(tmp_path / "nope.json")


def test_load_fleet_bad_json(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(FleetError, match="not valid JSON"):
        load_fleet(path)


def test_machine_lookup_errors():
    fleet = fleet_from_dict(BASE)
    with pytest.raises(FleetError, match="unknown machine"):
        fleet.machine("nope")
    with pytest.raises(FleetError, match="unknown region"):
        fleet.region("nope")
    with pytest.raises(FleetError, match="unknown workload"):
        fleet.workload("nope")
