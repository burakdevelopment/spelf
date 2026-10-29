import json
import math
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = pathlib.Path(__file__).resolve().parent
BENCH = HERE.parent

VARIANTS = ["Spelf (GCC)", "C (GCC)", "Ada", "SPARK", "Rust", "Rust+ovf", "Spelf (Clang)", "C (Clang)"]
COLOR = dict(zip(VARIANTS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]))
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b", "none": "#e1e0d9"}
WL_LABEL = {"adcs": "ADCS\n(f64)", "ccsds": "CCSDS\nheader", "crc16": "CRC-16", "px4": "PX4 rate\n(f32)",
            "fbw": "Fighter\nFBW", "bdot": "CubeSat\nB-dot", "lander": "Mars\nlander"}
TG_LABEL = {"rv32": "RV32IMAC (soft-float)", "r5": "Cortex-R5F (VFPv3-D16)", "leon3": "LEON3 (SPARC V8)"}

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 9, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "axes.titlesize": 10, "axes.titlecolor": INK, "axes.titleweight": "bold", "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "figure.dpi": 100,
    "savefig.dpi": 200, "savefig.bbox": "tight", "lines.linewidth": 2.0, "figure.facecolor": "white",
    "axes.facecolor": "white",
})

def save(fig, name):
    fig.savefig(HERE / f"{name}.png")
    fig.savefig(HERE / f"{name}.pdf")
    plt.close(fig)
    print("wrote", name)

def load(p):
    p = BENCH / p
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def gmean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else None

def fig_relative(res, field, name, ylabel, title):
    wls = [w for w in WL_LABEL if w in res]
    targets = [t for t in ("rv32", "r5", "leon3") if any(k.endswith("| " + t) for w in wls for k in res[w]["cells"])]
    fig, axes = plt.subplots(len(targets), 1, figsize=(9.2, 2.6 * len(targets)), sharex=True)
    axes = axes if len(targets) > 1 else [axes]
    for ax, t in zip(axes, targets):
        base_v = "C (GCC)" if t != "leon3" else "C (Clang)"
        present = [v for v in VARIANTS if any(res[w]["cells"].get(f"{v} | {t}", {}).get("status") == "ok" for w in wls)]
        n = len(present)
        width = 0.8 / n
        ratios = {v: [] for v in present}
        for i, w in enumerate(wls + ["geomean"]):
            for j, v in enumerate(present):
                if w == "geomean":
                    val = gmean(ratios[v])
                else:
                    c = res[w]["cells"].get(f"{v} | {t}", {})
                    b = res[w]["cells"].get(f"{base_v} | {t}", {})
                    val = (c[field] / b[field]) if c.get("status") == "ok" and b.get("status") == "ok" and b[field] else None
                    if val:
                        ratios[v].append(val)
                if val:
                    ax.bar(i - 0.4 + width * (j + 0.5), val, width * 0.86, color=COLOR[v], label=v if i == 0 else None)
        ax.axhline(1.0, color=INK2, linewidth=0.8)
        ax.set_ylabel(ylabel)
        ax.set_title(f"{TG_LABEL[t]}  (baseline = {base_v})", loc="left")
        ax.set_xticks(range(len(wls) + 1))
        ax.set_xticklabels([WL_LABEL[w] for w in wls] + ["geo.\nmean"])
        ax.grid(axis="x", visible=False)
    handles = [Patch(color=COLOR[v], label=v) for v in VARIANTS if any(
        res[w]["cells"].get(f"{v} | {t}", {}).get("status") == "ok" for w in wls for t in targets)]
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.legend(handles=handles, loc="upper center", ncol=8, bbox_to_anchor=(0.5, 0.965), fontsize=8)
    fig.suptitle(title, y=0.995, fontsize=11, fontweight="bold", color=INK)
    save(fig, name)

def fig_checks(res):
    wls = [w for w in WL_LABEL if w in res]
    vs = [v for v in VARIANTS if v not in ("Spelf (Clang)", "C (Clang)")]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    tot = []
    for v in vs:
        tot.append(sum(res[w]["cells"].get(f"{v} | r5", {}).get("checks", 0) or 0 for w in wls))
    bars = ax.barh(range(len(vs)), tot, color=[COLOR[v] for v in vs], height=0.6)
    for b, x in zip(bars, tot):
        ax.text(b.get_width() + 0.3, b.get_y() + b.get_height() / 2, str(x), va="center", color=INK, fontsize=8)
    ax.set_yticks(range(len(vs)))
    ax.set_yticklabels(vs)
    ax.invert_yaxis()
    ax.set_xlabel("run-time check sites left in the compiled object (sum over 7 workloads, Cortex-R5F)")
    ax.set_title("Residual run-time checks after compilation", loc="left")
    ax.grid(axis="y", visible=False)
    save(fig, "residual_checks")

def fig_histograms(res):
    panels = [("fbw", "r5"), ("lander", "rv32"), ("px4", "r5"), ("adcs", "rv32")]
    panels = [(w, t) for w, t in panels if w in res and any("per_call" in c for c in res[w]["cells"].values())]
    if not panels:
        print("skip histograms: no per-call data (re-run spacecraft/run.py)")
        return
    vs = ["Spelf (GCC)", "C (GCC)", "Ada", "SPARK", "Rust"]
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 5.6))
    for ax, (w, t) in zip(axes.flat, panels):
        data = {v: res[w]["cells"].get(f"{v} | {t}", {}).get("per_call") for v in vs}
        data = {v: d for v, d in data.items() if d}
        lo = min(min(d) for d in data.values())
        hi = max(max(d) for d in data.values())
        bins = 30
        step = max(1, (hi - lo) / bins)
        edges = [lo + k * step for k in range(bins + 2)]
        for k, (v, d) in enumerate(data.items()):
            ax.hist(d, bins=edges, histtype="step", linewidth=[4.2, 3.0, 2.0, 1.5, 1.1][k], color=COLOR[v],
                    label=v, zorder=2 + k)
        ax.set_title(f"{WL_LABEL[w].replace(chr(10), ' ')} on {TG_LABEL[t].split(' ')[0]}", loc="left")
        ax.set_xlabel("instructions per call")
        ax.set_ylabel("calls")
        ax.grid(axis="x", visible=False)
    for ax in list(axes.flat)[len(panels):]:
        ax.set_visible(False)
    axes.flat[0].legend(fontsize=7.5, loc="upper right")
    fig.suptitle("Distribution of executed instructions per controller call (Unicorn, exact count)",
                 fontsize=11, fontweight="bold", color=INK)
    fig.tight_layout()
    save(fig, "per_call_histograms")

MUTANT_EN = {
    "M-01": "Unguarded f64 → i16 narrowing (Ariane 501)", "M-02": "Unit swap lbf·s ↔ N·s (Mars Climate Orbiter)",
    "M-03": "Frame swap Body ↔ ECI", "M-04": "Signed 32-bit centisecond counter overflow (787 GCU)",
    "M-05": "Epoch mix MET ↔ TAI (Deep Impact)", "M-06": "Buffer over-read trusting a length field",
    "M-07": "CCSDS length field: forgotten '+1'", "M-08": "Hand-written wrong ceiling priority (Pathfinder)",
    "M-09": "Unprotected state shared by two tasks", "M-10": "Saturated measurement used directly (Schiaparelli)",
    "M-11": "Last good value persists without age limit", "M-12": "Transient latched without debounce (MPL)",
    "M-13": "'Queue full' result ignored (Spirit, sol 18)", "M-14": "Backup channel shares primary's code (Ariane 501)",
    "M-15": "Halt on fault during flight phase (Ariane 501)", "M-16": "Out-of-range configuration constant (Titan IVB)",
    "M-17": "Error quaternion not canonicalised (long way round)", "M-18": "Clamped command used blind (integrator windup)",
    "M-19": "Reuse outside the verified envelope (Ariane 501)", "M-20": "Safe-mode path depends on failed gyro",
    "M-21": "Recursion without declared bound (stack depth)", "M-22": "Uninitialised read",
    "M-23": "Division by a computed zero", "M-24": "Implicit rounding in fixed point",
    "M-25": "Three channels, unsigned link, one Byzantine channel", "M-26": "Second spike passes filter at hold expiry (QF72)",
}
SYM_COLOR = {"✓": STATUS["good"], "◆": "#7fd17f", "R": STATUS["warning"], "R◆": STATUS["warning"],
             "✗": STATUS["critical"], "?": "#c3c2b7", "—": "#f0efec"}

def fig_mutants(ml):
    langs = ["C", "MISRA C", "Rust", "Ada", "SPARK", "Spelf"]
    ids = sorted(ml)
    fig, ax = plt.subplots(figsize=(6.2, 0.26 * len(ids) + 1.4))
    for i, m in enumerate(ids):
        for j, l in enumerate(langs):
            s = ml[m]["cells"].get(l, ["—"])[0]
            ax.add_patch(plt.Rectangle((j + 0.04, i + 0.06), 0.92, 0.88, color=SYM_COLOR.get(s, "#f0efec"), lw=0))
            ax.text(j + 0.5, i + 0.5, s, ha="center", va="center", fontsize=8,
                    color="white" if s in ("✓", "✗") else INK)
    ax.set_xlim(0, len(langs))
    ax.set_ylim(len(ids), 0)
    ax.set_xticks([j + 0.5 for j in range(len(langs))])
    ax.set_xticklabels(langs)
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(ids))])
    ax.set_yticklabels([f"{m}  {MUTANT_EN.get(m, ml[m]['title'])}" for m in ids], fontsize=7)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    handles = [Patch(color=SYM_COLOR["✓"], label="✓ static, unconditional"),
               Patch(color=SYM_COLOR["◆"], label="◆ static, needs a declaration"),
               Patch(color=SYM_COLOR["R"], label="R caught only at run time"),
               Patch(color=SYM_COLOR["✗"], label="✗ not caught")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=2, fontsize=7.5)
    ax.set_title("26 accident-derived defects, real tool output per language", loc="left", pad=22)
    save(fig, "mutant_matrix")

    fig, ax = plt.subplots(figsize=(6.4, 2.5))
    order = [("✓", "static, unconditional"), ("◆", "static, with declaration"), ("R", "run time only"),
             ("✗", "not caught")]
    for j, l in enumerate(langs):
        left = 0
        for s, _ in order:
            n = sum(1 for m in ids if ml[m]["cells"].get(l, ["—"])[0] in ((s,) if s != "R" else ("R", "R◆")))
            if n:
                ax.barh(j, n, left=left, color=SYM_COLOR[s], height=0.62, edgecolor="white", linewidth=1.5)
                if n >= 2:
                    ax.text(left + n / 2, j, str(n), ha="center", va="center", fontsize=7.5,
                            color="white" if s in ("✓", "✗") else INK)
            left += n
    ax.set_yticks(range(len(langs)))
    ax.set_yticklabels(langs)
    ax.invert_yaxis()
    ax.set_xlim(0, len(ids))
    ax.set_xlabel(f"number of defects (of {len(ids)})")
    ax.grid(axis="y", visible=False)
    ax.legend(handles=[Patch(color=SYM_COLOR[s], label=f"{s} {t}") for s, t in order], ncol=4, fontsize=7.5,
              loc="upper center", bbox_to_anchor=(0.5, 1.22))
    save(fig, "detection_summary")

def fig_real_bugs():
    import csv
    p = BENCH / "real-bugs" / "real_bugs.csv"
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        syms = [r["spelf"] for r in csv.DictReader(f) if r["sample"] == "B"]
    counts = [(s, syms.count(s)) for s in ("✓", "◆", "✗")]
    labels = {"✓": "caught, no extra declaration", "◆": "caught with one declaration (unit, frame, state…)",
              "✗": "outside any language rule (logic, hardware, requirements)"}
    fig, ax = plt.subplots(figsize=(6.4, 1.2))
    left = 0
    for s, n in counts:
        ax.barh(0, n, left=left, color=SYM_COLOR[s], height=0.5, edgecolor="white", linewidth=2)
        ax.text(left + n / 2, 0, f"{s} {n}", ha="center", va="center", color="white" if s != "◆" else INK,
                fontsize=9, fontweight="bold")
        left += n
    ax.set_xlim(0, len(syms))
    ax.set_yticks([])
    ax.set_xlabel(f"fixed flight-code bugs, unbiased sample of {len(syms)} merged ArduPilot BUG pull requests")
    ax.grid(False)
    ax.legend(handles=[Patch(color=SYM_COLOR[s], label=f"{s} {labels[s]}") for s, _ in counts], fontsize=7.5,
              loc="upper center", bbox_to_anchor=(0.5, -0.75), ncol=1)
    save(fig, "real_bugs")

REF_LABEL = "all 8 builds (C, Ada, SPARK, Rust, Spelf), bit-identical"
SIL_MUT_EN = {"FBW-M1": "mean instead of median in sensor voting",
              "FBW-M2": "integrator not frozen when elevator saturates",
              "BDOT-M1": "magnetometer used without rotation to body frame",
              "LND-M1": "transient leg contact written straight to a flag (MPL)",
              "LND-M2": "invalid radar value used without checking status"}
MUT_COLOR = ["#eb6834", "#6250d6"]

def shade(ax, spans):
    for a, b, txt in spans:
        ax.axvspan(a, b, color="#f0efec", lw=0)

def fig_fbw(sc):
    r = sc["fbw"]
    ref = r["reference"]
    muts = [(k, v) for k, v in r.get("mutants", {}).items()]
    fig, axes = plt.subplots(3, 1, figsize=(8.6, 6.4), sharex=True)
    series = [("alpha", "angle of attack [deg]"), ("q", "pitch rate [deg/s]"), ("defl", "elevator [deg]")]
    for ax, (key, lab) in zip(axes, series):
        ax.axvspan(9, 12, color="#f0efec", lw=0)
        ax.plot(ref["t"], [math.degrees(x) for x in ref[key]], color="#2a78d6", label=REF_LABEL)
        for (mid, m), col in zip(muts, MUT_COLOR):
            cl = m["c_closed_loop"]
            ax.plot(cl["t"], [math.degrees(x) for x in cl[key]], color=col, linewidth=1.4,
                    label=f"{mid} in C: {SIL_MUT_EN.get(mid, m['title'])}")
        ax.set_ylabel(lab)
        if key == "alpha":
            ax.set_ylim(-40, 40)
        if key == "q":
            ax.set_ylim(-60, 60)
    for x, txt in ((4, "gyro 3 hard-over"), (9, "wind shear"), (16, "AoA 1 stuck")):
        axes[0].axvline(x, color=MUTED, linewidth=0.8, linestyle=":")
        axes[0].text(x + 0.1, 34, txt, fontsize=7, color=INK2)
    m1 = r.get("mutants", {}).get("FBW-M1")
    if m1 and m1["c_closed_loop"]["departed"]:
        axes[0].annotate(f"FBW-M1 departs: |α| reaches {m1['c_closed_loop']['max_alpha_deg']:.0f}°", xy=(12.8, -40),
                         xytext=(13.3, -16), fontsize=7.5, color="#eb6834",
                         arrowprops={"arrowstyle": "->", "color": "#eb6834", "lw": 0.8})
    axes[-1].set_xlabel("time [s]")
    h, l = axes[0].get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.suptitle("Fighter fly-by-wire pitch channel, unstable airframe, 80 Hz", x=0.075, y=0.995, ha="left",
                 fontsize=10, fontweight="bold", color=INK)
    fig.legend(h, l, loc="upper left", bbox_to_anchor=(0.07, 0.965), fontsize=7.5, ncol=1)
    save(fig, "sil_fbw")

def fig_bdot(sc):
    r = sc["bdot"]
    ref = r["reference"]
    fig, ax = plt.subplots(figsize=(8.6, 3.0))
    ax.plot([t / 60 for t in ref["t"]], ref["rate_dps"], color="#2a78d6", label=REF_LABEL)
    for (mid, m), col in zip(r.get("mutants", {}).items(), MUT_COLOR):
        cl = m["c_closed_loop"]
        ax.plot([t / 60 for t in cl["t"]], cl["rate_dps"], color=col, linewidth=1.4,
                label=f"{mid} in C: {SIL_MUT_EN.get(mid, m['title'])}")
    ax.set_yscale("log")
    ax.set_xlabel("time [min]")
    ax.set_ylabel("body rate |ω| [deg/s]")
    ax.legend(fontsize=7.5, loc="lower left")
    ax.set_title("3U CubeSat B-dot detumbling, magnetometer mounted 90° to body", loc="left")
    save(fig, "sil_bdot")

def fig_lander(sc):
    r = sc["lander"]
    ref = r["reference"]
    fig, axes = plt.subplots(2, 1, figsize=(8.6, 4.8), sharex=True)
    for ax, key, lab in ((axes[0], "h", "altitude [m]"), (axes[1], "v", "vertical speed [m/s]")):
        ax.plot(ref["t"], ref[key], color="#2a78d6", label=REF_LABEL + f" — touchdown {abs(ref['touchdown_v']):.2f} m/s")
        for (mid, m), col in zip(r.get("mutants", {}).items(), MUT_COLOR):
            cl = m["c_closed_loop"]
            n = next((i for i, h in enumerate(cl["h"]) if h <= 0.0), len(cl["h"]))
            k = n + 1 if key == "h" else n
            ax.plot(cl["t"][:k], cl[key][:k], color=col, linewidth=1.4,
                    label=f"{mid} in C: engines cut at {cl['engine_cut_alt']:.0f} m, impact {abs(cl['touchdown_v']):.1f} m/s")
        ax.set_ylabel(lab)
    for x, txt in ((2.0, "leg-deploy shock"), (9.0, "radar dropout"), (13.0, "leg contact noise")):
        axes[0].axvline(x, color=MUTED, linewidth=0.8, linestyle=":")
        axes[0].text(x + 0.2, 380, txt, fontsize=7, color=INK2)
    axes[1].set_xlabel("time [s]")
    axes[1].legend(fontsize=7, loc="lower right")
    axes[0].set_title("Mars lander terminal descent and touchdown detection, 100 Hz", loc="left")
    fig.tight_layout()
    save(fig, "sil_lander")

def fig_adcs(sil):
    if not sil:
        return
    ref = sil["reference"]
    mut = sil["mutant"]["c_rows"]
    fig, ax = plt.subplots(figsize=(8.6, 2.8))
    ax.plot([r[0] for r in ref], [r[1] for r in ref], color="#2a78d6", label=REF_LABEL)
    ax.plot([r[0] for r in mut], [r[1] for r in mut], color="#eb6834", linewidth=1.4,
            label="M-17 in C: error quaternion not canonicalised")
    ax.axvline(300, color=MUTED, linewidth=0.8, linestyle=":")
    ax.text(305, 150, "estimator flips q → −q", fontsize=7, color=INK2)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("pointing error [deg]")
    ax.legend(fontsize=7.5)
    ax.set_title("6U CubeSat attitude manoeuvre (160° slew), quaternion PD", loc="left")
    save(fig, "sil_adcs")

def main():
    res = load("spacecraft/results.json")
    if res:
        fig_relative(res, "insn_mean", "perf_relative", "instructions / call\n(relative)",
                     "Executed instructions per call, relative to flight-permitted C")
        fig_relative(res, "text", "size_relative", ".text bytes\n(relative)", "Object code size, relative to C")
        fig_checks(res)
        fig_histograms(res)
    ml = load("multilang/results.json")
    if ml:
        fig_mutants(ml)
    fig_real_bugs()
    sc = load("spacecraft/sil_complex_results.json")
    if sc:
        for k, f in (("fbw", fig_fbw), ("bdot", fig_bdot), ("lander", fig_lander)):
            if k in sc and "mutants" in sc[k]:
                f(sc)
    fig_adcs(load("spacecraft/sil_results.json"))

if __name__ == "__main__":
    main()
