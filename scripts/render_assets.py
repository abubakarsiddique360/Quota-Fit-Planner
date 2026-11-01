"""Regenerate README assets from real runs.

Usage:  python scripts/render_assets.py
Writes: docs/results.json, docs/images/plan.png,
        docs/images/cost-compare.png, docs/images/quota-utilisation.png
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quota_fit_planner.model import load_fleet  # noqa: E402
from quota_fit_planner.place import (  # noqa: E402
    balanced_fit,
    cheapest_first,
    demand_vs_capacity,
    impossible_by,
    spread_evenly,
)
from quota_fit_planner.report import (  # noqa: E402
    render_binding,
    render_capacity,
    render_compare,
    render_plan,
)

BG = "#0d1117"
CHROME = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#79c0ff"
STRATEGY_COLOURS = {
    "cheapest-first": "#0969da",
    "spread-evenly": "#8250df",
}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "courier.ttf", "DejaVuSansMono.ttf"):
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_terminal(text: str, out_path: Path, title: str) -> None:
    font = load_font(14)
    lines = text.expandtabs(4).splitlines() or [""]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    char_width = probe.textlength("M", font=font)
    line_height = 20
    pad = 24
    width = int(max(len(line) for line in lines) * char_width) + pad * 2
    height = pad * 2 + len(lines) * line_height + 44
    image = Image.new("RGB", (max(width, 800), height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 34], fill=CHROME)
    for index, colour in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse([16 + index * 22, 10, 28 + index * 22, 22], fill=colour)
    draw.text((92, 9), title, font=load_font(13), fill=MUTED)

    for row, line in enumerate(lines):
        colour = FG
        stripped = line.strip()
        if line.startswith("$"):
            colour = ACCENT
        elif stripped.startswith("balanced"):
            colour = "#1a7f37"
        elif stripped.startswith("spread"):
            colour = "#8250df"
        elif stripped.startswith("cheapest"):
            colour = "#d29922"
        elif "surplus" in stripped or "tight" in stripped or "SHORT" in stripped:
            colour = "#d29922" if "SHORT" in stripped else "#1a7f37"
        elif stripped.startswith(("region", "strategy", "quota", "=", "-")):
            colour = MUTED
        elif "risk" in stripped:
            colour = MUTED
        draw.text((pad, 50 + row * line_height), line, font=font, fill=colour)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def render_cost_compare(fleets: dict, out_path: Path) -> None:
    names = list(fleets)
    strategies = ("cheapest-first", "spread-evenly", "balanced")
    results = {
        name: {
            "cheapest-first": cheapest_first(fleets[name]),
            "spread-evenly": spread_evenly(fleets[name]),
            "balanced": balanced_fit(fleets[name]),
        }
        for name in names
    }

    figure, axes = plt.subplots(1, len(names), figsize=(11.0, 3.8), squeeze=False)
    figure.patch.set_facecolor("#ffffff")
    for axis, name in zip(axes[0], names, strict=True):
        plans = results[name]
        labels = list(strategies)
        costs = [plans[label].hourly_cost for label in labels]
        colours = [
            STRATEGY_COLOURS.get(label, "#1a7f37") for label in labels
        ]
        bars = axis.bar(range(len(labels)), costs, color=colours, width=0.55)
        for bar, label in zip(bars, labels, strict=True):
            plan = plans[label]
            mark = "" if plan.complete else "\nINCOMPLETE"
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                max(costs) * 0.03,
                f"{plan.placed_units}/{plan.requested_units}{mark}",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#ffffff",
            )
        axis.set_xticks(range(len(labels)))
        axis.set_xticklabels([label.replace("-", "\n") for label in labels], fontsize=7.5)
        axis.set_ylabel("$ per hour")
        axis.set_title(name, fontsize=9.5, loc="left")
        axis.set_ylim(0, max(costs) * 1.25)
        axis.grid(axis="y", linestyle=":", alpha=0.45)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
    figure.suptitle("Three strategies on two fleets (label = units placed)", fontsize=10.5, x=0.02,
                    ha="left")
    figure.tight_layout(rect=(0.02, 0, 1, 0.93))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, format="png", dpi=150)
    plt.close(figure)


def render_utilisation(plan, out_path: Path) -> None:
    regions = [region for region in plan.fleet.regions if plan.usage[region.name].units]
    dims = ["vcpu", "memory_gb", "gpus"]
    figure, axis = plt.subplots(figsize=(9.0, 3.6))
    figure.patch.set_facecolor("#ffffff")
    width = 0.26
    colours = {"vcpu": "#0969da", "memory_gb": "#1a7f37", "gpus": "#cf222e"}

    for index, dim in enumerate(dims):
        values = []
        for region in regions:
            capacity = region.quota(dim)
            used = plan.usage[region.name].used[dim]
            values.append((used / capacity * 100) if capacity else 0.0)
        positions = [p + index * width for p in range(len(regions))]
        bars = axis.bar(positions, values, width, color=colours[dim], label=dim)
        for bar, value in zip(bars, values, strict=True):
            if value > 6:
                axis.text(bar.get_x() + bar.get_width() / 2, value - 4, f"{value:.0f}",
                          ha="center", fontsize=6.5, color="#ffffff")

    axis.axhline(100, color="#24292f", linestyle="--", linewidth=0.9)
    axis.text(-0.45, 102, "quota ceiling", fontsize=7, color="#24292f")
    axis.set_xticks([p + width for p in range(len(regions))])
    axis.set_xticklabels([region.name for region in regions], fontsize=8)
    axis.set_ylabel("% of the regional quota used")
    axis.set_ylim(0, 118)
    axis.set_title(f"{plan.strategy}: where the fleet actually landed", fontsize=10, loc="left")
    axis.legend(fontsize=8, frameon=False, ncol=3, loc="lower right")
    axis.grid(axis="y", linestyle=":", alpha=0.45)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    figure.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, format="png", dpi=150)
    plt.close(figure)


def main() -> int:
    images = ROOT / "docs" / "images"
    images.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)

    fleets = {
        "examples/fleet.json": load_fleet("examples/fleet.json"),
        "examples/tight.json": load_fleet("examples/tight.json"),
    }
    main_fleet = fleets["examples/fleet.json"]
    tight = fleets["examples/tight.json"]

    plans = {
        "cheapest": cheapest_first(main_fleet),
        "spread": spread_evenly(main_fleet),
        "balanced": balanced_fit(main_fleet),
    }
    tight_plans = {
        "cheapest": cheapest_first(tight),
        "spread": spread_evenly(tight),
        "balanced": balanced_fit(tight),
    }

    payload = {
        "fleet": main_fleet.name,
        "capacity": demand_vs_capacity(main_fleet),
        "blockers": impossible_by(main_fleet),
        "plans": {name: plan.as_dict() for name, plan in plans.items()},
        "tight": {
            name: plan.as_dict() for name, plan in tight_plans.items()
        },
    }
    (ROOT / "docs" / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    terminal = (
        "$ quota-fit capacity --fleet examples/fleet.json\n\n"
        + render_capacity(main_fleet)
        + "\n\n$ quota-fit compare --fleet examples/fleet.json\n\n"
        + render_compare(list(plans.values()))
        + "\n\n$ quota-fit plan --fleet examples/fleet.json --strategy balanced\n\n"
        + render_plan(plans["balanced"])
        + "\n\n"
        + render_binding(plans["balanced"])
        + "\n\n$ quota-fit compare --fleet examples/tight.json\n\n"
        + render_compare(list(tight_plans.values()))
    )
    title = "quota-fit  -  three strategies, two fleets"
    render_terminal(terminal, images / "plan.png", title)
    render_cost_compare(fleets, images / "cost-compare.png")
    render_utilisation(plans["balanced"], images / "quota-utilisation.png")

    print(
        "main: "
        + " ".join(f"{name}=${plan.hourly_cost:.2f}" for name, plan in plans.items())
        + " | tight: "
        + " ".join(
            f"{name}={plan.placed_units}/{plan.requested_units}"
            for name, plan in tight_plans.items()
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
