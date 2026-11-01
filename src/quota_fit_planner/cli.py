"""Command line entry point for quota-fit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .model import FleetError, load_fleet
from .place import (
    balanced_fit,
    cheapest_first,
    demand_vs_capacity,
    impossible_by,
    spread_evenly,
)
from .report import (
    render_binding,
    render_capacity,
    render_compare,
    render_json,
    render_plan,
    render_regions,
    render_workloads,
)

DEFAULT_FLEET = "examples/fleet.json"
STRATEGIES = {
    "cheapest": cheapest_first,
    "spread": spread_evenly,
    "balanced": balanced_fit,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quota-fit",
        description="Place a requested cloud fleet under per-region quotas at the lowest cost.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="place the fleet with one strategy")
    plan.add_argument("--fleet", default=DEFAULT_FLEET)
    plan.add_argument("--strategy", choices=tuple(STRATEGIES), default="balanced")
    plan.add_argument("--format", choices=("text", "json"), default="text")

    cmp_cmd = sub.add_parser("compare", help="run all three strategies and show the gap")
    cmp_cmd.add_argument("--fleet", default=DEFAULT_FLEET)
    cmp_cmd.add_argument("--format", choices=("text", "json"), default="text")

    cap = sub.add_parser("capacity", help="aggregate demand against aggregate capacity")
    cap.add_argument("--fleet", default=DEFAULT_FLEET)
    cap.add_argument("--format", choices=("text", "json"), default="text")

    reg = sub.add_parser("regions", help="region table with quotas and price index")
    reg.add_argument("--fleet", default=DEFAULT_FLEET)

    work = sub.add_parser("workloads", help="workload table with total demand")
    work.add_argument("--fleet", default=DEFAULT_FLEET)

    bind = sub.add_parser("binding", help="which quotas decided the answer")
    bind.add_argument("--fleet", default=DEFAULT_FLEET)
    bind.add_argument("--strategy", choices=tuple(STRATEGIES), default="balanced")

    out = sub.add_parser("export", help="write the placement as JSON")
    out.add_argument("--fleet", default=DEFAULT_FLEET)
    out.add_argument("--strategy", choices=tuple(STRATEGIES), default="balanced")
    out.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        fleet = load_fleet(args.fleet)
    except FleetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "regions":
        print(render_regions(fleet))
        return 0

    if args.command == "workloads":
        print(render_workloads(fleet))
        return 0

    if args.command == "capacity":
        totals = demand_vs_capacity(fleet)
        blockers = impossible_by(fleet)
        if args.format == "json":
            payload = {
                "totals": totals,
                "blockers": [{"workload": w, "quota": q} for w, q in blockers],
            }
            print(render_json(payload))
        else:
            print(render_capacity(fleet))
            if blockers:
                print()
                print("workloads that cannot fit even in their own region:")
                for workload, quota in blockers:
                    print(f"  {workload}: needs more {quota} than the region allows")
        return 1 if blockers else 0

    if args.command == "compare":
        plans = [factory(fleet) for factory in STRATEGIES.values()]
        if args.format == "json":
            print(render_json([plan.as_dict() for plan in plans]))
        else:
            print(render_compare(plans))
            print()
            for plan in plans:
                print(render_plan(plan))
                print()
        return 0 if all(plan.complete for plan in plans) else 1

    if args.command == "binding":
        plan = STRATEGIES[args.strategy](fleet)
        print(render_binding(plan))
        return 0

    plan = STRATEGIES[args.strategy](fleet)

    if args.command == "export":
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **plan.as_dict(),
            "placements": [placement.as_dict() for placement in plan.placements],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {target} ({plan.placed_units} placements)")
        return 0 if plan.complete else 1

    if args.format == "json":
        print(render_json(plan.as_dict()))
    else:
        print(render_plan(plan))
        print()
        print(render_binding(plan))
    return 0 if plan.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
