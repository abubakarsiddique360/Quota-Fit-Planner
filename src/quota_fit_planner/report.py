"""Text reports for placements."""

from __future__ import annotations

import json

from .model import QUOTA_KEYS, Fleet
from .place import Plan, demand_vs_capacity


def render_plan(plan: Plan) -> str:
    lines = [f"Placement: {plan.strategy}", "=" * 74]
    lines.append(
        f"units {plan.placed_units}/{plan.requested_units}   "
        f"hourly ${plan.hourly_cost:,.2f}   monthly ${plan.monthly_cost:,.0f}   "
        f"{'complete' if plan.complete else 'INCOMPLETE'}"
    )
    lines.append("")
    header = (
        f"{'region':<14}{'units':>7}{'vcpu':>8}{'memory':>10}{'gpus':>7}{'ips':>6}{'$/hour':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for region in plan.fleet.regions:
        usage = plan.usage[region.name]
        if usage.units == 0:
            continue
        lines.append(
            f"{region.name[:13]:<14}{usage.units:>7}{usage.used['vcpu']:>8.0f}"
            f"{usage.used['memory_gb']:>10.0f}{usage.used['gpus']:>7.0f}"
            f"{usage.used['public_ips']:>6.0f}{usage.hourly_cost:>10.2f}"
        )
    if plan.unplaced:
        lines.append("")
        lines.append("unplaced:")
        for workload, count in plan.unplaced:
            lines.append(f"  {workload}: {count} unit(s)")
    return "\n".join(lines)


def render_compare(plans: list[Plan]) -> str:
    header = (
        f"{'strategy':<20}{'units':>7}{'hourly':>11}{'monthly':>12}{'vs best':>10}{'fit':>6}"
    )
    lines = ["Three strategies, same fleet", "=" * len(header), header]
    complete_costs = [plan.hourly_cost for plan in plans if plan.complete]
    best = min(complete_costs) if complete_costs else 0.0
    for plan in plans:
        if plan.complete and complete_costs:
            gap = "-" if abs(plan.hourly_cost - best) < 1e-9 else (
                f"+${(plan.hourly_cost - best) * plan.fleet.hours_per_month:,.0f}"
            )
        else:
            gap = "n/a"
        lines.append(
            f"{plan.strategy[:19]:<20}{plan.placed_units:>7}{plan.hourly_cost:>11,.2f}"
            f"{plan.monthly_cost:>12,.0f}{gap:>10}"
            f"{('yes' if plan.complete else 'NO'):>6}"
        )
    return "\n".join(lines)


def render_capacity(fleet: Fleet) -> str:
    totals = demand_vs_capacity(fleet)
    header = f"{'quota':<12}{'demand':>10}{'capacity':>11}{'used':>8}  verdict"
    lines = ["Aggregate demand against aggregate capacity", "=" * len(header), header]
    for key in QUOTA_KEYS:
        row = totals[key]
        fraction = row["fraction"]
        verdict = "surplus" if fraction < 0.8 else "tight" if fraction <= 1.0 else "SHORT"
        lines.append(
            f"{key:<12}{row['demand']:>10,.0f}{row['capacity']:>11,.0f}"
            f"{fraction * 100:>7.0f}%  {verdict}"
        )
    lines.append("")
    lines.append("a quota above 100% cannot be placed by any strategy - it needs a quota increase")
    return "\n".join(lines)


def render_regions(fleet: Fleet) -> str:
    header = (
        f"{'region':<14}{'price':>7}{'vcpu':>7}{'memory':>9}{'gpus':>7}{'ips':>6}  allowed for"
    )
    lines = ["Regions", "=" * len(header), header]
    for region in fleet.regions:
        lines.append(
            f"{region.name[:13]:<14}{region.price_index:>7.2f}{region.quotas['vcpu']:>7.0f}"
            f"{region.quotas['memory_gb']:>9.0f}{region.quotas['gpus']:>7.0f}"
            f"{region.quotas['public_ips']:>6.0f}  "
            f"{', '.join(region.allowed_for) or 'anything'}"
        )
    return "\n".join(lines)


def render_workloads(fleet: Fleet) -> str:
    header = f"{'workload':<18}{'machine':<12}{'count':>6}{'vcpu':>7}{'memory':>9}{'gpus':>7}"
    lines = ["Workloads", "=" * len(header), header]
    for workload in fleet.workloads:
        demand = fleet.demand(workload.name)
        lines.append(
            f"{workload.name[:17]:<18}{workload.machine[:11]:<12}{workload.count:>6}"
            f"{demand['vcpu']:>7.0f}{demand['memory_gb']:>9.0f}{demand['gpus']:>7.0f}"
        )
    return "\n".join(lines)


def render_binding(plan: Plan) -> str:
    bound = plan.binding_quotas()
    lines = ["Quotas at or above 99% utilisation", "=" * 58]
    if not bound:
        lines.append("none - every region still has room")
        return "\n".join(lines)
    for region, quota, fraction in bound:
        lines.append(f"  {region:<14}{quota:<12}{fraction * 100:>6.1f}%")
    lines.append("")
    lines.append("these are the quotas that decided the shape of the answer")
    return "\n".join(lines)


def render_json(payload) -> str:
    return json.dumps(payload, indent=2)
