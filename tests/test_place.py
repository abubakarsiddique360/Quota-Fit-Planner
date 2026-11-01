import pytest

from quota_fit_planner.model import QUOTA_KEYS, fleet_from_dict
from quota_fit_planner.place import (
    balanced_fit,
    cheapest_first,
    demand_vs_capacity,
    impossible_by,
    spread_evenly,
)


def build(**overrides):
    """Mirrors examples/tight.json: cheap vCPU is exactly enough for the small units only."""
    payload = {
        "name": "tiny",
        "machine_types": [
            {"name": "small", "vcpu": 2, "memory_gb": 4, "hourly_usd": 0.10},
            {"name": "big", "vcpu": 4, "memory_gb": 32, "hourly_usd": 0.40},
        ],
        "regions": [
            {"name": "cheap", "price_index": 0.5,
             "quotas": {"vcpu": 16, "memory_gb": 128, "gpus": 0, "public_ips": 16},
             "allowed_for": ["web", "app"]},
            {"name": "dear", "price_index": 1.0,
             "quotas": {"vcpu": 16, "memory_gb": 64, "gpus": 0, "public_ips": 16}},
        ],
        "workloads": [
            {"name": "web", "machine": "small", "count": 8},
            {"name": "app", "machine": "big", "count": 4},
        ],
    }
    payload.update(overrides)
    return fleet_from_dict(payload)


def test_cheapest_first_fills_the_cheap_region():
    plan = cheapest_first(build())
    assert plan.usage["cheap"].units > 0
    assert plan.usage["cheap"].hourly_cost > 0


def test_cheapest_first_can_strand_capacity():
    plan = cheapest_first(build())
    assert not plan.complete
    assert dict(plan.unplaced)["app"] == 2


def test_spread_evenly_uses_more_regions():
    spread = spread_evenly(build())
    cheapest = cheapest_first(build())
    assert spread.usage["dear"].units >= cheapest.usage["dear"].units


def test_balanced_fit_completes_the_tight_fleet():
    plan = balanced_fit(build())
    assert plan.complete
    assert plan.placed_units == plan.requested_units == 12


def test_balanced_fit_beats_or_matches_cheapest_first_when_both_complete():
    fleet = build()
    balanced = balanced_fit(fleet)
    cheapest = cheapest_first(fleet)
    assert balanced.complete
    if cheapest.complete:
        assert balanced.hourly_cost <= cheapest.hourly_cost + 1e-9


def test_quotas_are_never_exceeded():
    for strategy in (cheapest_first, spread_evenly, balanced_fit):
        plan = strategy(build())
        for region in plan.fleet.regions:
            usage = plan.usage[region.name]
            for key in QUOTA_KEYS:
                assert usage.used[key] <= region.quota(key) + 1e-9


def test_pinned_workload_stays_put():
    fleet = build()
    payload = {
        "name": fleet.name,
        "machine_types": [machine.as_dict() for machine in fleet.machine_types],
        "regions": [region.as_dict() for region in fleet.regions],
        "workloads": [
            {"name": "web", "machine": "small", "count": 2},
            {"name": "app", "machine": "big", "count": 1, "pinned_region": "dear"},
        ],
    }
    plan = balanced_fit(fleet_from_dict(payload))
    app_regions = {item.region for item in plan.placements if item.workload == "app"}
    assert app_regions == {"dear"}


def test_binding_quotas_reports_saturated_dimensions():
    plan = balanced_fit(build())
    bound = {(region, quota) for region, quota, _fraction in plan.binding_quotas()}
    assert ("cheap", "vcpu") in bound


def test_utilisation_is_a_fraction():
    plan = balanced_fit(build())
    for value in plan.utilisation().values():
        assert 0.0 <= value <= 1.0


def test_monthly_cost_uses_the_hourly_rate():
    plan = balanced_fit(build())
    assert plan.monthly_cost == pytest.approx(plan.hourly_cost * 730)


def test_demand_vs_capacity_flags_a_shortage():
    payload = {
        "machine_types": [{"name": "big", "vcpu": 32, "memory_gb": 256, "hourly_usd": 1.0}],
        "regions": [{"name": "only", "price_index": 1.0,
                     "quotas": {"vcpu": 8, "memory_gb": 8, "gpus": 0, "public_ips": 2}}],
        "workloads": [{"name": "monster", "machine": "big", "count": 1}],
    }
    fleet = fleet_from_dict(payload)
    totals = demand_vs_capacity(fleet)
    assert totals["vcpu"]["fraction"] > 1.0
    assert impossible_by(fleet) == [("monster", "vcpu"), ("monster", "memory_gb")]


def test_impossible_by_empty_when_capacity_exists():
    assert impossible_by(build()) == []


def test_plan_serialises():
    payload = balanced_fit(build()).as_dict()
    assert payload["complete"] is True
    assert payload["placed_units"] == 12
    assert set(payload["utilisation"]) == set(QUOTA_KEYS)
    assert payload["regions"]["cheap"]["capacity"]["vcpu"] == 16


def test_single_region_fleet():
    payload = {
        "machine_types": [{"name": "small", "vcpu": 2, "memory_gb": 8, "hourly_usd": 0.1}],
        "regions": [{"name": "solo", "price_index": 1.0,
                     "quotas": {"vcpu": 8, "memory_gb": 32, "gpus": 0, "public_ips": 8}}],
        "workloads": [{"name": "web", "machine": "small", "count": 4}],
    }
    plan = balanced_fit(fleet_from_dict(payload))
    assert plan.complete
    assert plan.usage["solo"].units == 4


def test_zero_gpu_workloads_ignore_the_gpu_quota():
    plan = cheapest_first(build())
    assert plan.usage["cheap"].used["gpus"] == 0


def test_balanced_reports_which_order_won():
    plan = balanced_fit(build())
    assert plan.strategy.startswith("balanced (")
