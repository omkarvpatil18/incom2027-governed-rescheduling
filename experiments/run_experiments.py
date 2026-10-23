"""
INCOM 2027 Experiment Runner
Governed vs Ungoverned Autonomy in Production Rescheduling

Instances:   LA21 (15x10, 150 ops) and LA36 (15x15, 225 ops)
             Lawrence benchmark family, OR-Library (Beasley 1990)
Disruptions: breakdown, rush_order, delay, shortage
             10 scenarios per type per instance (seed 99)
Strategies:  governed | always_replan | random_replan | threshold_only
References:
  - Vieira et al. (2003) J Scheduling 6(1):39-62  [disruption frequencies]
  - Aytug et al. (2005) EJOR 161(1):86-110        [window parameters]
  - Sabuncuoglu & Bayiz (2000) EJOR 126(3):567-86 [window start ranges]

Usage:
    cd experiments/
    python run_experiments.py

Output:
    ../results/incom2027_la_results.csv
    ../results/incom2027_la_results.json
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'instances'))

import random, json, csv, time
from collections import defaultdict
from la_instances_real import INSTANCES_LA
from framework import (
    build_operations, compute_job_precedences, decode_schedule,
    refinement_stage, build_machine_criticality, count_resequenced_ops,
    DisruptionEvent, ExperimentResult, THRESHOLDS,
    compute_severity, check_feasibility_violation, Operation,
    governance_decision
)
from framework_fast import greedy_baseline

# ── Experiment configuration ─────────────────────────────────────────────────
SEED        = 99
N_SCENARIOS = 10
STRATEGIES  = ["governed", "always_replan", "random_replan", "threshold_only"]
DTYPES      = ["breakdown", "rush_order", "delay", "shortage"]

# Published disruption parameters
# Window start/duration as fraction of baseline makespan
DISRUPTION_PARAMS = {
    "breakdown":  {"ws": (0.10, 0.70), "wl": (0.08, 0.25), "freq": 0.35},
    "delay":      {"ws": (0.10, 0.70), "wl": (0.08, 0.25), "freq": 0.30},
    "rush_order": {"ws": (0.10, 0.60), "wl": (0.05, 0.15), "freq": 0.25},
    "shortage":   {"ws": (0.10, 0.70), "wl": (0.08, 0.25), "freq": 0.10},
}


# ── Scenario generation ───────────────────────────────────────────────────────

def generate_scenarios(n_machines, base_makespan, rng, dtype, n=10):
    """Generate n disruption scenarios using published parameter ranges."""
    p = DISRUPTION_PARAMS[dtype]
    out = []
    for i in range(n):
        m  = rng.randint(0, n_machines - 1)
        ws = rng.randint(int(p["ws"][0] * base_makespan),
                         int(p["ws"][1] * base_makespan))
        wl = rng.randint(int(p["wl"][0] * base_makespan),
                         int(p["wl"][1] * base_makespan))
        out.append(DisruptionEvent(
            event_id=f"e{i}", event_type=dtype,
            affected_machine=m, window_start=ws,
            window_end=ws + max(wl, 5)))
    return out


def generate_rush_job(ops, n_machines, rng):
    """Generate a rush job sized relative to instance mean operation duration."""
    mean_dur = sum(op.duration for op in ops.values()) / len(ops)
    n_ops    = rng.randint(2, 4)
    machines = rng.sample(range(n_machines), n_ops)
    jid      = f"jr{rng.randint(10, 99)}"
    ro, rp, prev = {}, [], None
    for k, m in enumerate(machines):
        dur = max(5, int(mean_dur * rng.uniform(0.5, 1.5)))
        oid = f"{jid}o{k}"
        ro[oid] = Operation(op_id=oid, job_id=jid, machine=m, duration=dur)
        if prev:
            rp.append((prev, oid))
        prev = oid
    return ro, rp


# ── Rescheduler (greedy SPT + critical-block refinement) ──────────────────────

def reschedule(ops, jprecs, disrupted, rush_ops=None, rush_precs=None,
               rng=None, max_iter=150):
    """Greedy-SPT rescheduler with disruption awareness and refinement."""
    all_ops   = dict(ops)
    all_precs = list(jprecs)
    if rush_ops:
        all_ops.update(rush_ops)
    if rush_precs:
        all_precs.extend(rush_precs)
    raw_order = greedy_baseline(all_ops, {}, all_precs, rng)
    return refinement_stage(all_ops, all_precs, raw_order,
                            disrupted or {}, max_iter=max_iter)


# ── Strategy runners ──────────────────────────────────────────────────────────

def run_governed(ev, ops, jprecs, base_order, base_comp,
                 crit_map, rng, ro=None, rp=None):
    it   = governance_decision(ev, ops, {}, base_comp, crit_map, "interpretation")
    rpl  = (it.outcome == "replan")
    dis  = {ev.affected_machine: (ev.window_start, ev.window_end)}
    fo   = base_order
    if rpl:
        ro2 = ro if ev.event_type == "rush_order" else None
        rp2 = rp if ev.event_type == "rush_order" else None
        d   = None if ev.event_type == "rush_order" else dis
        fo, fc, fm = reschedule(ops, jprecs, d, ro2, rp2, rng)
    else:
        fc, fm, _ = decode_schedule(ops, jprecs, base_order, dis)
    eg   = governance_decision(ev, ops, {}, fc, crit_map, "execution_governance")
    he   = (eg.outcome == "human_approval")
    rq   = count_resequenced_ops(base_order, fo)
    sv   = compute_severity(ev, ops, {}, base_comp, crit_map)
    vl   = check_feasibility_violation(ev, ops, base_comp)
    ei   = "replan" if (sv >= THRESHOLDS["interpretation"] or vl) else "continue"
    s2   = compute_severity(ev, ops, {}, fc, crit_map)
    ee   = "human_approval" if s2 >= THRESHOLDS["execution_governance"] else "autonomous"
    gc   = (it.outcome == ei) and (eg.outcome == ee)
    fm   = max(fc.values()) if fc else 0
    return fm, rpl, False, rq, he, gc


def run_always_replan(ev, ops, jprecs, base_order, base_comp,
                      crit_map, rng, ro=None, rp=None):
    dis = {ev.affected_machine: (ev.window_start, ev.window_end)}
    ro2 = ro if ev.event_type == "rush_order" else None
    rp2 = rp if ev.event_type == "rush_order" else None
    d   = None if ev.event_type == "rush_order" else dis
    fo, comp, ms = reschedule(ops, jprecs, d, ro2, rp2, rng)
    return ms, True, False, count_resequenced_ops(base_order, fo), False, False


def run_random_replan(ev, ops, jprecs, base_order, base_comp,
                      crit_map, rng, ro=None, rp=None):
    if rng.random() < 0.5:
        return run_always_replan(ev, ops, jprecs, base_order,
                                 base_comp, crit_map, rng, ro, rp)
    dis = {ev.affected_machine: (ev.window_start, ev.window_end)}
    comp, ms, _ = decode_schedule(ops, jprecs, base_order, dis)
    return ms, False, False, 0, False, False


def run_threshold_only(ev, ops, jprecs, base_order, base_comp,
                       crit_map, rng, ro=None, rp=None):
    sv  = compute_severity(ev, ops, {}, base_comp, crit_map)
    vl  = check_feasibility_violation(ev, ops, base_comp)
    dis = {ev.affected_machine: (ev.window_start, ev.window_end)}
    if sv >= THRESHOLDS["interpretation"] or vl:
        ro2 = ro if ev.event_type == "rush_order" else None
        rp2 = rp if ev.event_type == "rush_order" else None
        d   = None if ev.event_type == "rush_order" else dis
        fo, comp, ms = reschedule(ops, jprecs, d, ro2, rp2, rng)
        return ms, True, False, count_resequenced_ops(base_order, fo), False, False
    comp, ms, _ = decode_schedule(ops, jprecs, base_order, dis)
    return ms, False, False, 0, False, False


RUNNERS = {
    "governed":       run_governed,
    "always_replan":  run_always_replan,
    "random_replan":  run_random_replan,
    "threshold_only": run_threshold_only,
}


# ── Main experiment loop ──────────────────────────────────────────────────────

def run_all_experiments():
    rng     = random.Random(SEED)
    results = []
    t0      = time.time()

    for inst_name, inst_info in INSTANCES_LA.items():
        print(f"\n{'='*60}")
        print(f"Instance: {inst_name} ({inst_info['family']})")
        print(f"  {inst_info['n_jobs']} jobs x {inst_info['n_machines']} machines"
              f" = {inst_info['n_ops']} operations")
        print(f"  Published optimum: {inst_info['optimum']}")
        print('='*60)

        ops, jseqs = build_operations(inst_info["data"])
        jprecs     = compute_job_precedences(jseqs)
        crit_map   = build_machine_criticality(ops, inst_info["n_machines"])

        print("  Building baseline schedule (greedy SPT + refinement)...")
        raw      = greedy_baseline(ops, jseqs, jprecs, rng)
        bo, bc, bms = refinement_stage(ops, jprecs, raw, {}, max_iter=200)
        gap      = (bms - inst_info["optimum"]) / inst_info["optimum"] * 100
        print(f"  Baseline Cmax: {bms}  (gap from optimum: {gap:+.1f}%)")

        for dtype in DTYPES:
            freq      = DISRUPTION_PARAMS[dtype]["freq"]
            scenarios = generate_scenarios(inst_info["n_machines"], bms,
                                           rng, dtype, N_SCENARIOS)

            for s_idx, ev in enumerate(scenarios):
                dis      = {ev.affected_machine: (ev.window_start, ev.window_end)}
                _, nm, _ = decode_schedule(ops, jprecs, bo, dis)
                ro_s = rp_s = None
                if dtype == "rush_order":
                    ro_s, rp_s = generate_rush_job(ops, inst_info["n_machines"], rng)

                for strategy in STRATEGIES:
                    try:
                        ms, rpl, inf, rsq, he, gc = RUNNERS[strategy](
                            ev, ops, jprecs, bo, bc, crit_map, rng, ro_s, rp_s)
                    except Exception as ex:
                        print(f"  ERROR [{strategy}] {dtype} s{s_idx}: {ex}")
                        ms, rpl, inf, rsq, he, gc = nm, False, True, 0, False, False

                    delta = (nm - ms) / nm * 100 if nm > 0 else 0.0
                    results.append(ExperimentResult(
                        instance=inst_name, disruption_type=dtype,
                        scenario_id=s_idx, strategy=strategy,
                        makespan_naive=nm, makespan_response=ms,
                        makespan_delta_pct=round(delta, 2),
                        replan_triggered=rpl, infeasible_response=inf,
                        ops_resequenced=rsq, human_escalated=he,
                        governance_correct=gc))

            # Per-type summary
            type_results = [r for r in results
                            if r.instance == inst_name
                            and r.disruption_type == dtype]
            summary = " | ".join(
                f"{s[:3]}:{sum(r.makespan_delta_pct for r in type_results if r.strategy==s)/N_SCENARIOS:+.1f}%"
                for s in STRATEGIES)
            print(f"  [{dtype}] freq={freq:.0%} | {summary}")

    print(f"\nTotal time: {time.time()-t0:.1f}s")
    return results


# ── Export results ────────────────────────────────────────────────────────────

def export_results(results, out_dir="../results"):
    os.makedirs(out_dir, exist_ok=True)
    fields = ["instance", "disruption_type", "scenario_id", "strategy",
              "makespan_naive", "makespan_response", "makespan_delta_pct",
              "replan_triggered", "infeasible_response", "ops_resequenced",
              "human_escalated", "governance_correct"]
    csv_path  = os.path.join(out_dir, "incom2027_la_results.csv")
    json_path = os.path.join(out_dir, "incom2027_la_results.json")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: getattr(r, k) for k in fields})
    with open(json_path, "w") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)
    print(f"\nResults saved to {csv_path}")
    print(f"Results saved to {json_path}")


def print_summary(results):
    DTYPES_S  = ["breakdown", "rush_order", "delay", "shortage"]
    print("\n" + "="*72)
    print("RESULTS SUMMARY")
    print("="*72)
    print(f"\n{'Disruption':<14}{'Freq':>6}", end="")
    for s in STRATEGIES:
        print(f"  {s[:16]:<18}", end="")
    print()
    print("-"*80)
    for dtype in DTYPES_S:
        freq = DISRUPTION_PARAMS[dtype]["freq"]
        print(f"{dtype:<14}{freq:>6.0%}", end="")
        for s in STRATEGIES:
            sub = [r for r in results
                   if r.disruption_type == dtype and r.strategy == s]
            v   = sum(r.makespan_delta_pct for r in sub) / len(sub) if sub else 0
            print(f"  {v:>+7.1f}%          ", end="")
        print()
    print("\nHuman escalation (governed only):")
    for dtype in DTYPES_S:
        sub = [r for r in results
               if r.disruption_type == dtype and r.strategy == "governed"]
        hr  = sum(1 for r in sub if r.human_escalated) / len(sub) * 100 if sub else 0
        print(f"  {dtype:<16}: {hr:.0f}%")
    gov = [r for r in results if r.strategy == "governed"]
    acc = sum(1 for r in gov if r.governance_correct) / len(gov) * 100 if gov else 0
    print(f"\nGovernance accuracy: {acc:.1f}%")
    inf_rate = sum(1 for r in results if r.infeasible_response) / len(results) * 100
    print(f"Infeasibility rate:  {inf_rate:.1f}%")


if __name__ == "__main__":
    print("INCOM 2027 — Governed vs Ungoverned Autonomy")
    print(f"Instances: {list(INSTANCES_LA.keys())}")
    print(f"Seed: {SEED} | Scenarios per type: {N_SCENARIOS}")
    results = run_all_experiments()
    export_results(results)
    print_summary(results)
    print("\nDone.")
