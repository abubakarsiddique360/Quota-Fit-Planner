"""The shape of the placement problem: workloads, machine types, regions and their quotas."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

QUOTA_KEYS = ("vcpu", "memory_gb", "gpus", "public_ips")


class FleetError(ValueError):
    """Raised when a fleet description cannot be used."""


@dataclass(frozen=True)
class MachineType:
    name: str
    vcpu: int
    memory_gb: float
    gpus: int = 0
    hourly_usd: float = 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "vcpu": self.vcpu,
            "memory_gb": self.memory_gb,
            "gpus": self.gpus,
            "hourly_usd": self.hourly_usd,
        }


@dataclass(frozen=True)
class Region:
    name: str
    price_index: float
    quotas: Mapping[str, float]
    allowed_for: tuple[str, ...] = ()

    def quota(self, key: str) -> float:
        return float(self.quotas.get(key, 0))

    def allows(self, workload: str) -> bool:
        """An empty ``allowed_for`` means the region accepts anything."""
        return not self.allowed_for or workload in self.allowed_for

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "price_index": self.price_index,
            "quotas": dict(self.quotas),
            "allowed_for": list(self.allowed_for),
        }


@dataclass(frozen=True)
class Workload:
    name: str
    machine: str
    count: int
    pinned_region: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "machine": self.machine,
            "count": self.count,
            "pinned_region": self.pinned_region,
            "note": self.note,
        }


@dataclass(frozen=True)
class Fleet:
    name: str
    machine_types: tuple[MachineType, ...]
    regions: tuple[Region, ...]
    workloads: tuple[Workload, ...]
    hours_per_month: float = 730.0

    @property
    def machine_names(self) -> tuple[str, ...]:
        return tuple(machine.name for machine in self.machine_types)

    def machine(self, name: str) -> MachineType:
        for machine in self.machine_types:
            if machine.name == name:
                return machine
        raise FleetError(f"unknown machine type {name!r}")

    def region(self, name: str) -> Region:
        for region in self.regions:
            if region.name == name:
                return region
        raise FleetError(f"unknown region {name!r}")

    def workload(self, name: str) -> Workload:
        for workload in self.workloads:
            if workload.name == name:
                return workload
        raise FleetError(f"unknown workload {name!r}")

    def unit_cost(self, machine: str, region: str) -> float:
        return self.machine(machine).hourly_usd * self.region(region).price_index

    def demand(self, workload: str) -> dict[str, float]:
        """Total quota demand of one workload, across every unit it asks for."""
        item = self.workload(workload)
        machine = self.machine(item.machine)
        return {
            "vcpu": machine.vcpu * item.count,
            "memory_gb": machine.memory_gb * item.count,
            "gpus": machine.gpus * item.count,
            "public_ips": item.count,
        }

    def candidates(self, workload: str) -> tuple[str, ...]:
        """Regions a workload may run in, cheapest first (ties broken by name)."""
        item = self.workload(workload)
        if item.pinned_region:
            return (item.pinned_region,)
        allowed = [region.name for region in self.regions if region.allows(workload)]
        return tuple(
            sorted(allowed, key=lambda name: (self.unit_cost(item.machine, name), name))
        )


def _machine_from_dict(entry: Mapping) -> MachineType:
    if not entry.get("name"):
        raise FleetError("every machine type needs a name")
    vcpu = int(entry.get("vcpu", 0))
    memory = float(entry.get("memory_gb", 0))
    if vcpu <= 0 or memory <= 0:
        raise FleetError(f"machine {entry['name']!r} needs positive vcpu and memory_gb")
    price = float(entry.get("hourly_usd", 0))
    if price < 0:
        raise FleetError(f"machine {entry['name']!r} has a negative price")
    return MachineType(
        name=str(entry["name"]),
        vcpu=vcpu,
        memory_gb=memory,
        gpus=int(entry.get("gpus", 0)),
        hourly_usd=price,
    )


def _region_from_dict(entry: Mapping) -> Region:
    if not entry.get("name"):
        raise FleetError("every region needs a name")
    quotas = entry.get("quotas")
    if not isinstance(quotas, Mapping) or not quotas:
        raise FleetError(f"region {entry['name']!r} needs a quotas object")
    unknown = set(quotas) - set(QUOTA_KEYS)
    if unknown:
        raise FleetError(
            f"region {entry['name']!r} has unknown quota keys: {sorted(unknown)}"
        )
    cleaned: dict[str, float] = {}
    for key in QUOTA_KEYS:
        value = float(quotas.get(key, 0))
        if value < 0:
            raise FleetError(f"region {entry['name']!r} has a negative {key} quota")
        cleaned[key] = value
    index = float(entry.get("price_index", 1.0))
    if index <= 0:
        raise FleetError(f"region {entry['name']!r} has a non-positive price_index")
    allowed = entry.get("allowed_for") or ()
    if not isinstance(allowed, (list, tuple)):
        raise FleetError(f"region {entry['name']!r} allowed_for must be a list")
    return Region(
        name=str(entry["name"]),
        price_index=index,
        quotas=cleaned,
        allowed_for=tuple(str(item) for item in allowed),
    )


def fleet_from_dict(payload: Mapping) -> Fleet:
    if not isinstance(payload, Mapping):
        raise FleetError("fleet must be a JSON object")

    machines = payload.get("machine_types")
    if not isinstance(machines, list) or not machines:
        raise FleetError("machine_types must be a non-empty list")
    machine_types = tuple(_machine_from_dict(entry) for entry in machines)
    names = [machine.name for machine in machine_types]
    if len(set(names)) != len(names):
        raise FleetError("duplicate machine type name")

    regions_raw = payload.get("regions")
    if not isinstance(regions_raw, list) or not regions_raw:
        raise FleetError("regions must be a non-empty list")
    regions = tuple(_region_from_dict(entry) for entry in regions_raw)
    region_names = [region.name for region in regions]
    if len(set(region_names)) != len(region_names):
        raise FleetError("duplicate region name")

    workloads_raw = payload.get("workloads")
    if not isinstance(workloads_raw, list) or not workloads_raw:
        raise FleetError("workloads must be a non-empty list")
    workloads: list[Workload] = []
    seen: set[str] = set()
    for entry in workloads_raw:
        if not isinstance(entry, Mapping) or not entry.get("name"):
            raise FleetError("every workload needs a name")
        name = str(entry["name"])
        if name in seen:
            raise FleetError(f"duplicate workload {name!r}")
        seen.add(name)
        machine = entry.get("machine")
        if machine not in names:
            raise FleetError(f"workload {name!r} uses unknown machine {machine!r}")
        count = int(entry.get("count", 0))
        if count <= 0:
            raise FleetError(f"workload {name!r} needs a positive count")
        pinned = str(entry.get("pinned_region", ""))
        if pinned and pinned not in region_names:
            raise FleetError(f"workload {name!r} is pinned to unknown region {pinned!r}")
        workloads.append(
            Workload(
                name=name,
                machine=str(machine),
                count=count,
                pinned_region=pinned,
                note=str(entry.get("note", "")),
            )
        )

    hours = float(payload.get("hours_per_month", 730))
    if hours <= 0:
        raise FleetError("hours_per_month must be positive")

    return Fleet(
        name=str(payload.get("name", "fleet")),
        machine_types=machine_types,
        regions=regions,
        workloads=tuple(workloads),
        hours_per_month=hours,
    )


def load_fleet(path: Path | str) -> Fleet:
    source = Path(path)
    if not source.exists():
        raise FleetError(f"file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FleetError(f"not valid JSON at line {exc.lineno}: {exc.msg}") from exc
    return fleet_from_dict(payload)


@dataclass
class Usage:
    """Running quota totals for one region."""

    region: str
    used: dict[str, float] = field(default_factory=lambda: {key: 0.0 for key in QUOTA_KEYS})
    units: int = 0
    hourly_cost: float = 0.0

    def add(self, demand: Mapping[str, float], cost: float) -> None:
        for key in QUOTA_KEYS:
            self.used[key] += demand[key]
        self.units += 1
        self.hourly_cost += cost
