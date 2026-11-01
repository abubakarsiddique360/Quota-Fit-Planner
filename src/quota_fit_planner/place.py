"""Three placement strategies and an honest verdict on whether the fleet fits at all.

The problem is a multi-dimensional generalised assignment: every unit needs vCPU, memory, GPU and
public IPs from one region, and every region has a hard cap on each. It is NP-hard, so the tool
does not pretend to find the optimum - it compares three strategies that a human would actually
choose between, and reports the gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import QUOTA_KEYS, Fleet, Usage, Workload


@dataclass(frozen=True)
class Placement:
    """One unit of one workload, in one region."""

    workload: str
    machine: str
    region: str
    hourly_cost: float

    def as_dict(self) -> dict:
        return {
            "workload": self.workload,
            "machine": self.machine,
            "region": self.region,
            "hourly_cost": round(self.hourly_cost, 4),
        }


@dataclass(frozen=True)
class Plan:
    strategy: str
    fleet: Fleet
    placements: tuple[Placement, ...]
    unplaced: tuple[tuple[str, int], ...]
    usage: dict[str, Usage]

    @property
    def placed_units(self) -> int:
        return len(self.placements)

    @property
    def requested_units(self) -> int:
        return sum(workload.count for workload in self.fleet.workloads)

    @property
    def complete(self) -> bool:
        return not self.unplaced

    @property
    def hourly_cost(self) -> float:
        return sum(placement.hourly_cost for placement in self.placements)

    @property
    def monthly_cost(self) -> float:
        return self.hourly_cost * self.fleet.hours_per_month

    def binding_quotas(self) -> list[tuple[str, str, float]]:
        """(region, quota, utilisation fraction) for every quota at or above 99%."""
        bound: list[tuple[str, str, float]] = []
        for region in self.fleet.regions:
            usage = self.usage[region.name]
            for key in QUOTA_KEYS:
                capacity = region.quota(key)
                if capacity <= 0:
                    continue
                fraction = usage.used[key] / capacity
                if fraction >= 0.99:
                    bound.append((region.name, key, fraction))
        return sorted(bound, key=lambda item: (-item[2], item[0], item[1]))

    def utilisation(self) -> dict[str, float]:
        """Average quota utilisation across regions that offer each quota."""
        totals: dict[str, list[float]] = {key: [] for key in QUOTA_KEYS}
        for region in self.fleet.regions:
            usage = self.usage[region.name]
            for key in QUOTA_KEYS:
                capacity = region.quota(key)
                if capacity > 0:
                    totals[key].append(usage.used[key] / capacity)
        return {
            key: (sum(values) / len(values) if values else 0.0) for key, values in totals.items()
        }

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "placed_units": self.placed_units,
            "requested_units": self.requested_units,
            "complete": self.complete,
            "unplaced": [{"workload": name, "count": count} for name, count in self.unplaced],
            "hourly_cost": round(self.hourly_cost, 4),
            "monthly_cost": round(self.monthly_cost, 2),
            "utilisation": {key: round(value, 4) for key, value in self.utilisation().items()},
            "binding_quotas": [
                {"region": region, "quota": quota, "fraction": round(fraction, 4)}
                for region, quota, fraction in self.binding_quotas()
            ],
            "regions": {
                region.name: {
                    "units": self.usage[region.name].units,
                    "used": {
                        key: round(value, 3)
                        for key, value in self.usage[region.name].used.items()
                    },
                    "capacity": dict(region.quotas),
                }
                for region in self.fleet.regions
            },
        }


def _fits(region_quotas, used: dict[str, float], demand: dict[str, float]) -> bool:
    for key in QUOTA_KEYS:
        if used[key] + demand[key] > region_quotas[key] + 1e-9:
            return False
    return True


def _usage_for(fleet: Fleet) -> dict[str, Usage]:
    return {region.name: Usage(region=region.name) for region in fleet.regions}


def _single_unit(fleet: Fleet, workload: str) -> dict[str, float]:
    demand = fleet.demand(workload)
    count = fleet.workload(workload).count
    return {key: value / count for key, value in demand.items()}


def _place(fleet: Fleet, strategy: str, order: list[str], pick) -> Plan:
    """Shared driver: walk the workload units in ``order`` and let ``pick`` choose a region."""
    usage = _usage_for(fleet)
    placements: list[Placement] = []
    unplaced: dict[str, int] = {}
    unit_demand = {workload: _single_unit(fleet, workload) for workload in order}

    for workload in order:
        item = fleet.workload(workload)
        for _ in range(item.count):
            choice = pick(fleet, workload, usage, unit_demand[workload])
            if choice is None:
                unplaced[workload] = unplaced.get(workload, 0) + 1
                continue
            usage[choice].add(unit_demand[workload], fleet.unit_cost(item.machine, choice))
            placements.append(
                Placement(
                    workload=workload,
                    machine=item.machine,
                    region=choice,
                    hourly_cost=fleet.unit_cost(item.machine, choice),
                )
            )

    return Plan(
        strategy=strategy,
        fleet=fleet,
        placements=tuple(placements),
        unplaced=tuple(sorted(unplaced.items())),
        usage=usage,
    )


def _default_order(fleet: Fleet) -> list[str]:
    ordered = sorted(fleet.workloads, key=lambda item: (-item.count, item.name))
    return [item.name for item in ordered]


def _footprint_order(fleet: Fleet, *, biggest_first: bool = True) -> list[str]:
    """Order by how much one unit of the workload asks for, not by how many units there are.

    This is the order that saves a tight fleet: place the demanding workloads while every region
    is still empty, then let the small ones fill the gaps.
    """
    def key(workload: Workload):
        demand = _single_unit(fleet, workload.name)
        return (
            -demand["memory_gb"] if biggest_first else demand["memory_gb"],
            -demand["vcpu"] if biggest_first else demand["vcpu"],
            workload.name,
        )

    return [workload.name for workload in sorted(fleet.workloads, key=key)]


def _cheapest_pick(fleet: Fleet, workload: str, usage, demand):
    for candidate in fleet.candidates(workload):
        if _fits(fleet.region(candidate).quotas, usage[candidate].used, demand):
            return candidate
    return None


def cheapest_first(fleet: Fleet) -> Plan:
    """Put every unit in the cheapest region that still has room.

    This is what a spreadsheet does: sort regions by price, fill the cheapest, spill over. It
    finds the cheapest answer when nothing is scarce and quietly produces a bad one when the
    cheap region fills up with small units before a big one is considered.
    """
    return _place(fleet, "cheapest-first", _default_order(fleet), _cheapest_pick)


def spread_evenly(fleet: Fleet) -> Plan:
    """Deal units into regions round-robin, cheapest first.

    This is the strategy people reach for when they are afraid of exhausting one region: it keeps
    every region lightly loaded and every region's price mix identical. It is stable and it is
    almost never the cheapest.
    """
    order = [item.name for item in sorted(fleet.workloads, key=lambda item: item.name)]
    cursor = {"index": 0}

    def pick(region_fleet: Fleet, workload: str, usage, demand):
        candidates = region_fleet.candidates(workload)
        if not candidates:
            return None
        start = cursor["index"]
        for offset in range(len(candidates)):
            candidate = candidates[(start + offset) % len(candidates)]
            if _fits(region_fleet.region(candidate).quotas, usage[candidate].used, demand):
                cursor["index"] = (start + offset + 1) % len(candidates)
                return candidate
        return None

    return _place(fleet, "spread-evenly", order, pick)


def balanced_fit(fleet: Fleet) -> Plan:
    """Try several placement orders, keep the best, then repair what is left.

    Greedy placement is order-sensitive: sorting by unit count fills a region with small instances
    and then finds nowhere for the big ones. So the same cheapest-first rule is run over three
    orders - by count, by one-unit footprint (largest first), and by footprint (smallest first) -
    and the best result wins: complete before cheaper, cheaper before cheaper-still.

    After that, two repair passes run on the winner: price moves, then single-unit evictions for
    anything still stranded.
    """
    orders = {
        "count": _default_order(fleet),
        "bulk": _footprint_order(fleet, biggest_first=True),
        "slim": _footprint_order(fleet, biggest_first=False),
    }
    candidates = [
        _place(fleet, f"cheapest-first/{name}", order, _cheapest_pick)
        for name, order in orders.items()
    ]
    candidates.append(spread_evenly(fleet))

    def rank(plan: Plan) -> tuple:
        return (0 if plan.complete else 1, round(plan.hourly_cost, 6), plan.strategy)

    best = min(candidates, key=rank)
    placements = list(best.placements)
    usage = {
        name: Usage(region=name, used=dict(item.used), units=item.units,
                    hourly_cost=item.hourly_cost)
        for name, item in best.usage.items()
    }
    unplaced = dict(best.unplaced)

    _shift_to_cheaper(fleet, placements, usage)
    _evict_for_stranded(fleet, placements, usage, unplaced)

    source = best.strategy.split("/")[-1]
    return Plan(
        strategy=f"balanced ({source})",
        fleet=fleet,
        placements=tuple(placements),
        unplaced=tuple(sorted((name, count) for name, count in unplaced.items() if count > 0)),
        usage=usage,
    )


def _detach(usage: dict[str, Usage], placement: Placement, demand: dict[str, float]) -> None:
    record = usage[placement.region]
    for key in QUOTA_KEYS:
        record.used[key] -= demand[key]
    record.units -= 1
    record.hourly_cost -= placement.hourly_cost


def _attach(
    usage: dict[str, Usage], region: str, demand: dict[str, float], cost: float
) -> None:
    usage[region].add(demand, cost)


def _shift_to_cheaper(fleet: Fleet, placements: list[Placement], usage: dict[str, Usage]) -> None:
    improved = True
    while improved:
        improved = False
        for index, placement in enumerate(placements):
            workload = fleet.workload(placement.workload)
            if workload.pinned_region:
                continue
            demand = _single_unit(fleet, placement.workload)
            current_cost = fleet.unit_cost(placement.machine, placement.region)
            for candidate in fleet.candidates(placement.workload):
                if candidate == placement.region:
                    continue
                if fleet.unit_cost(placement.machine, candidate) >= current_cost:
                    continue
                if not _fits(fleet.region(candidate).quotas, usage[candidate].used, demand):
                    continue
                _detach(usage, placement, demand)
                cost = fleet.unit_cost(placement.machine, candidate)
                _attach(usage, candidate, demand, cost)
                placements[index] = Placement(
                    workload=placement.workload,
                    machine=placement.machine,
                    region=candidate,
                    hourly_cost=cost,
                )
                improved = True
                break
            if improved:
                break


def _evict_for_stranded(
    fleet: Fleet, placements: list[Placement], usage: dict[str, Usage], unplaced: dict[str, int]
) -> None:
    """Move one occupant to make room for a unit that had nowhere to go."""
    for _round in range(8):
        progress = False
        for workload_name in sorted(unplaced):
            while unplaced[workload_name] > 0:
                demand = _single_unit(fleet, workload_name)
                machine = fleet.workload(workload_name).machine
                best: tuple[float, int, str, str] | None = None
                for candidate in fleet.candidates(workload_name):
                    quotas = fleet.region(candidate).quotas
                    if _fits(quotas, usage[candidate].used, demand):
                        best = (fleet.unit_cost(machine, candidate), -1, candidate, "")
                        break
                    for index, occupant in enumerate(placements):
                        if occupant.region != candidate:
                            continue
                        occupant_workload = fleet.workload(occupant.workload)
                        if occupant.workload == workload_name or occupant_workload.pinned_region:
                            continue
                        occupant_demand = _single_unit(fleet, occupant.workload)
                        freed = {
                            key: usage[candidate].used[key] - occupant_demand[key]
                            for key in QUOTA_KEYS
                        }
                        if not _fits(quotas, freed, demand):
                            continue
                        for escape in fleet.candidates(occupant.workload):
                            if escape == candidate:
                                continue
                            if not _fits(
                                fleet.region(escape).quotas, usage[escape].used, occupant_demand
                            ):
                                continue
                            delta = fleet.unit_cost(machine, candidate) - occupant.hourly_cost + (
                                fleet.unit_cost(occupant.machine, escape)
                            )
                            key = (delta, index, candidate, escape)
                            if best is None or key < best:
                                best = key
                            break

                if best is None:
                    break
                _delta, index, candidate, escape = best
                if index >= 0:
                    occupant = placements[index]
                    occupant_demand = _single_unit(fleet, occupant.workload)
                    _detach(usage, occupant, occupant_demand)
                    escape_cost = fleet.unit_cost(occupant.machine, escape)
                    _attach(usage, escape, occupant_demand, escape_cost)
                    placements[index] = Placement(
                        workload=occupant.workload,
                        machine=occupant.machine,
                        region=escape,
                        hourly_cost=escape_cost,
                    )
                cost = fleet.unit_cost(machine, candidate)
                _attach(usage, candidate, demand, cost)
                placements.append(
                    Placement(
                        workload=workload_name,
                        machine=machine,
                        region=candidate,
                        hourly_cost=cost,
                    )
                )
                unplaced[workload_name] -= 1
                progress = True
        if not progress:
            break


def demand_vs_capacity(fleet: Fleet) -> dict[str, dict[str, float]]:
    """Aggregate demand against aggregate capacity, per quota dimension.

    This is the feasibility pre-check: if the whole fleet needs more vCPU than every allowed region
    can supply, no placement strategy can help, and the answer is a bigger quota rather than a
    better schedule.
    """
    totals: dict[str, dict[str, float]] = {}
    for key in QUOTA_KEYS:
        demand = sum(fleet.demand(workload.name)[key] for workload in fleet.workloads)
        capacity = sum(region.quota(key) for region in fleet.regions)
        totals[key] = {
            "demand": demand,
            "capacity": capacity,
            "fraction": (demand / capacity) if capacity else float("inf"),
        }
    return totals


def impossible_by(fleet: Fleet) -> list[tuple[str, str]]:
    """(workload, quota) pairs that can never be satisfied, regardless of strategy."""
    blockers: list[tuple[str, str]] = []
    for workload in fleet.workloads:
        demand = fleet.demand(workload.name)
        candidates = fleet.candidates(workload.name)
        for key in QUOTA_KEYS:
            if demand[key] == 0:
                continue
            best_single = max(
                (fleet.region(name).quota(key) for name in candidates), default=0.0
            )
            if best_single < demand[key] and len(candidates) == 1:
                blockers.append((workload.name, key))
    return blockers
