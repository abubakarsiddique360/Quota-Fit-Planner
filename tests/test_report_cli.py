import json

import pytest

from quota_fit_planner.cli import main
from quota_fit_planner.model import fleet_from_dict, load_fleet
from quota_fit_planner.place import balanced_fit, cheapest_first, spread_evenly
from quota_fit_planner.report import (
    render_binding,
    render_capacity,
    render_compare,
    render_json,
    render_plan,
    render_regions,
    render_workloads,
)

PAYLOAD = {
    "name": "tiny",
    "machine_types": [{"name": "small", "vcpu": 2, "memory_gb": 8, "hourly_usd": 0.10}],
    "regions": [
        {"name": "cheap", "price_index": 0.5,
         "quotas": {"vcpu": 8, "memory_gb": 32, "gpus": 0, "public_ips": 8}},
        {"name": "dear", "price_index": 1.0,
         "quotas": {"vcpu": 8, "memory_gb": 32, "gpus": 0, "public_ips": 8}},
    ],
    "workloads": [{"name": "web", "machine": "small", "count": 4}],
}


@pytest.fixture
def fleet_file(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    return path


def test_render_plan_lists_regions():
    fleet = fleet_from_dict(PAYLOAD)
    text = render_plan(balanced_fit(fleet))
    assert "units 4/4" in text
    assert "cheap" in text


def test_render_plan_shows_unplaced():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["workloads"][0]["count"] = 20
    text = render_plan(cheapest_first(fleet_from_dict(payload)))
    assert "unplaced" in text


def test_render_compare_marks_the_gap():
    fleet = fleet_from_dict(PAYLOAD)
    text = render_compare([cheapest_first(fleet), spread_evenly(fleet), balanced_fit(fleet)])
    assert "vs best" in text
    assert "strategy" in text


def test_render_capacity_verdicts():
    text = render_capacity(fleet_from_dict(PAYLOAD))
    assert "surplus" in text
    assert "quota" in text


def test_render_regions_and_workloads():
    fleet = fleet_from_dict(PAYLOAD)
    assert "allowed for" in render_regions(fleet)
    assert "web" in render_workloads(fleet)


def test_render_binding_when_empty():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["workloads"][0]["count"] = 2
    text = render_binding(balanced_fit(fleet_from_dict(payload)))
    assert "none" in text


def test_render_binding_when_saturated():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["workloads"][0]["count"] = 4
    payload["regions"][0]["quotas"]["vcpu"] = 8
    text = render_binding(balanced_fit(fleet_from_dict(payload)))
    assert "%" in text


def test_json_report_round_trips():
    assert json.loads(render_json({"a": 1}))["a"] == 1


def test_cli_regions(fleet_file, capsys):
    assert main(["regions", "--fleet", str(fleet_file)]) == 0
    assert "cheap" in capsys.readouterr().out


def test_cli_workloads(fleet_file, capsys):
    assert main(["workloads", "--fleet", str(fleet_file)]) == 0
    assert "web" in capsys.readouterr().out


def test_cli_capacity(fleet_file, capsys):
    assert main(["capacity", "--fleet", str(fleet_file)]) == 0
    assert "surplus" in capsys.readouterr().out


def test_cli_capacity_flags_blockers(tmp_path, capsys):
    payload = {
        "machine_types": [{"name": "huge", "vcpu": 64, "memory_gb": 512, "hourly_usd": 4.0}],
        "regions": [{"name": "only", "price_index": 1.0,
                     "quotas": {"vcpu": 8, "memory_gb": 8, "gpus": 0, "public_ips": 2}}],
        "workloads": [{"name": "monster", "machine": "huge", "count": 1}],
    }
    path = tmp_path / "short.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["capacity", "--fleet", str(path)]) == 1
    assert "cannot fit" in capsys.readouterr().out


def test_cli_capacity_json(fleet_file, capsys):
    assert main(["capacity", "--fleet", str(fleet_file), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "totals" in payload
    assert payload["blockers"] == []


def test_cli_plan_text(fleet_file, capsys):
    assert main(["plan", "--fleet", str(fleet_file)]) == 0
    assert "Placement:" in capsys.readouterr().out


def test_cli_plan_json(fleet_file, capsys):
    assert main(["plan", "--fleet", str(fleet_file), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert payload["placed_units"] == 4


def test_cli_plan_each_strategy(fleet_file, capsys):
    for strategy in ("cheapest", "spread", "balanced"):
        assert main(["plan", "--fleet", str(fleet_file), "--strategy", strategy]) == 0
        capsys.readouterr()


def test_cli_plan_incomplete_exits_one(tmp_path, capsys):
    payload = json.loads(json.dumps(PAYLOAD))
    payload["workloads"][0]["count"] = 20
    path = tmp_path / "overflow.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["plan", "--fleet", str(path), "--strategy", "cheapest"]) == 1
    capsys.readouterr()


def test_cli_compare(fleet_file, capsys):
    assert main(["compare", "--fleet", str(fleet_file)]) == 0
    output = capsys.readouterr().out
    assert "cheapest-first" in output
    assert "spread-evenly" in output


def test_cli_compare_json(fleet_file, capsys):
    assert main(["compare", "--fleet", str(fleet_file), "--format", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_cli_binding(fleet_file, capsys):
    assert main(["binding", "--fleet", str(fleet_file)]) == 0
    assert "Quotas at or above" in capsys.readouterr().out


def test_cli_export(fleet_file, tmp_path, capsys):
    target = tmp_path / "out" / "placement.json"
    assert main(["export", "--fleet", str(fleet_file), "--out", str(target)]) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["placed_units"] == 4
    assert payload["placements"]
    assert "wrote" in capsys.readouterr().out


def test_cli_missing_file(tmp_path, capsys):
    assert main(["plan", "--fleet", str(tmp_path / "nope.json")]) == 2
    assert "file not found" in capsys.readouterr().err


def test_cli_invalid_fleet(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    assert main(["plan", "--fleet", str(path)]) == 2
    assert "machine_types" in capsys.readouterr().err


def test_shipped_fleet_is_placeable():
    fleet = load_fleet("examples/fleet.json")
    plan = balanced_fit(fleet)
    assert plan.complete
    assert plan.placed_units == 36


def test_shipped_fleet_tight_case_needs_the_portfolio():
    fleet = load_fleet("examples/tight.json")
    assert not cheapest_first(fleet).complete
    assert balanced_fit(fleet).complete


def test_shipped_fleet_spread_is_more_expensive():
    fleet = load_fleet("examples/fleet.json")
    assert spread_evenly(fleet).hourly_cost > balanced_fit(fleet).hourly_cost
