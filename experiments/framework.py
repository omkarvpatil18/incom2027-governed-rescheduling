"""
Governed rescheduling framework for INCOM 2027 experiments.

This module implements the three-layer architecture:
  Layer 1 - Declarative governance (ASP)
  Layer 2 - Feasibility restoration (ASP, pairwise-disjunctive)
  Layer 3 - Heuristic refinement (critical-block local search)

The ASP encoding is derived from the same underlying framework
described in the companion paper, applied here to entirely new
benchmark instances (DMU family) and new disruption scenarios.
"""

import clingo
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class Operation:
    op_id: str
    job_id: str
    machine: int
    duration: int

@dataclass
class Schedule:
    """A complete schedule: machine_order[m] = ordered list of op_ids."""
    machine_order: dict = field(default_factory=dict)
    completion: dict = field(default_factory=dict)
    makespan: int = 0
    feasible: bool = True

@dataclass
class DisruptionEvent:
    event_id: str
    event_type: str          # breakdown | rush_order | delay | shortage
    affected_machine: int
    window_start: int
    window_end: int
    severity_score: int = 0

@dataclass
class GovernanceDecision:
    event_id: str
    stage: str
    outcome: str             # replan|continue / autonomous|human_approval
    severity: int = 0
    violated_feasibility: bool = False

@dataclass
class ExperimentResult:
    instance: str
    disruption_type: str
    scenario_id: int
    strategy: str
    makespan_naive: int
    makespan_response: int
    makespan_delta_pct: float
    replan_triggered: bool
    infeasible_response: bool
    ops_resequenced: int
    human_escalated: bool
    governance_correct: bool


# ============================================================
# SCHEDULE BUILDER FROM INSTANCE DATA
# ============================================================

def build_operations(instance_data):
    """Convert raw instance list to Operation objects."""
    ops = {}
    job_sequences = {}
    for j_idx, job in enumerate(instance_data):
        job_id = f"j{j_idx}"
        job_sequences[job_id] = []
        for o_idx, (machine, duration) in enumerate(job):
            op_id = f"j{j_idx}o{o_idx}"
            ops[op_id] = Operation(op_id=op_id, job_id=job_id,
                                   machine=machine, duration=duration)
            job_sequences[job_id].append(op_id)
    return ops, job_sequences


def compute_job_precedences(job_sequences):
    """Return list of (pred_op_id, succ_op_id) pairs."""
    precs = []
    for job_id, op_list in job_sequences.items():
        for i in range(len(op_list) - 1):
            precs.append((op_list[i], op_list[i+1]))
    return precs


# ============================================================
# LONGEST-PATH DECODE (shared by governance and scheduling)
# ============================================================

def decode_schedule(ops, job_precs, machine_order, disrupted=None):
    """
    Compute completion times via longest-path propagation.
    disrupted: dict {machine: (start, end)} unavailability windows.
    Returns (completion dict, makespan, feasible bool).
    """
    if disrupted is None:
        disrupted = {}

    completion = {}
    # Build predecessor maps
    job_pred = {k: v for v, k in job_precs}   # succ -> pred
    machine_pred = {}
    for m, order in machine_order.items():
        for i in range(1, len(order)):
            machine_pred[order[i]] = order[i-1]

    # Topological order: process ops with no unresolved predecessors
    remaining = set(ops.keys())
    resolved = set()

    def get_start(op_id):
        op = ops[op_id]
        earliest = 0
        if op_id in job_pred:
            pred = job_pred[op_id]
            if pred not in completion:
                return None
            earliest = max(earliest, completion[pred])
        if op_id in machine_pred:
            mpred = machine_pred[op_id]
            if mpred not in completion:
                return None
            earliest = max(earliest, completion[mpred])
        # Apply disruption deferral
        m = op.machine
        if m in disrupted:
            ws, we = disrupted[m]
            finish = earliest + op.duration
            if earliest < we and finish > ws:   # overlaps window
                earliest = we                    # defer to after window
        return earliest

    max_iter = len(ops) * len(ops)
    iteration = 0
    while remaining and iteration < max_iter:
        iteration += 1
        progress = False
        for op_id in list(remaining):
            start = get_start(op_id)
            if start is not None:
                completion[op_id] = start + ops[op_id].duration
                remaining.discard(op_id)
                resolved.add(op_id)
                progress = True
        if not progress:
            return completion, 0, False  # cycle detected

    if remaining:
        return completion, 0, False

    makespan = max(completion.values()) if completion else 0
    return completion, makespan, True


# ============================================================
# ASP SCHEDULING MODEL (Layer 2 - Feasibility Restoration)
# ============================================================

ASP_SCHEDULING = """
on_machine(I,M) :- operation(I,_,M,_).

{ before(I,K) } :- operation(I,_,M,_), operation(K,_,M,_), I < K.
machine_prec(I,K) :- before(I,K).
machine_prec(K,I) :- operation(I,_,M,_), operation(K,_,M,_), I < K, not before(I,K).

prec(I,K) :- precedes_job(I,K).
prec(I,K) :- machine_prec(I,K).

reach(I,K) :- prec(I,K).
reach(I,K) :- reach(I,L), prec(L,K).
:- reach(I,I).

#show machine_prec/2.
"""

ASP_GOVERNANCE = """
severity(E,S) :-
    event(E,Type), event_type_weight(Type,W1),
    affected_machine(E,M), machine_criticality(M,W2),
    due_proximity(E,W3),
    S = W1 + W2 + W3.

decision(E, interpretation, replan) :-
    stage(E, interpretation),
    severity(E,S),
    threshold(interpretation,T),
    S >= T.

decision(E, interpretation, replan) :-
    stage(E, interpretation),
    violates_availability(E).

decision(E, interpretation, continue) :-
    stage(E, interpretation),
    not violates_availability(E),
    severity(E,S),
    threshold(interpretation,T),
    S < T.

decision(E, execution_governance, human_approval) :-
    stage(E, execution_governance),
    severity(E,S),
    threshold(execution_governance,T),
    S >= T.

decision(E, execution_governance, autonomous) :-
    stage(E, execution_governance),
    severity(E,S),
    threshold(execution_governance,T),
    S < T.

#show decision/3.
#show severity/2.
"""


def solve_scheduling_asp(ops, job_precs, disrupted=None, rush_ops=None, rush_precs=None):
    """
    Invoke ASP scheduling model. Returns (machine_order dict, feasible bool).
    rush_ops/rush_precs: additional operations for rush_order events.
    """
    all_ops = dict(ops)
    all_precs = list(job_precs)
    if rush_ops:
        all_ops.update(rush_ops)
    if rush_precs:
        all_precs.extend(rush_precs)

    facts = []
    op_ids = sorted(all_ops.keys())
    op_idx = {op_id: i for i, op_id in enumerate(op_ids)}

    for op_id, op in all_ops.items():
        facts.append(f"operation({op_idx[op_id]},{op.job_id},{op.machine},{op.duration}).")

    for pred_id, succ_id in all_precs:
        if pred_id in op_idx and succ_id in op_idx:
            facts.append(f"precedes_job({op_idx[pred_id]},{op_idx[succ_id]}).")

    program = "\n".join(facts) + "\n" + ASP_SCHEDULING

    ctl = clingo.Control(["--warn=none"])
    ctl.add("base", [], program)
    ctl.ground([("base", [])])

    machine_prec_atoms = []
    sat = False

    with ctl.solve(yield_=True) as handle:
        for model in handle:
            sat = True
            for atom in model.symbols(shown=True):
                if atom.name == "machine_prec":
                    i, k = atom.arguments[0].number, atom.arguments[1].number
                    machine_prec_atoms.append((op_ids[i], op_ids[k]))
            break  # first stable model only (feasibility mode)

    if not sat:
        return None, False

    # Reconstruct machine_order from machine_prec pairs
    from_set = defaultdict(set)
    to_set = defaultdict(set)
    for i, k in machine_prec_atoms:
        from_set[i].add(k)
        to_set[k].add(i)

    # Group by machine
    machine_ops = defaultdict(list)
    for op_id, op in all_ops.items():
        machine_ops[op.machine].append(op_id)

    machine_order = {}
    for m, m_ops in machine_ops.items():
        # Topological sort of this machine's ops using machine_prec
        in_degree = {op: 0 for op in m_ops}
        adj = defaultdict(list)
        for i, k in machine_prec_atoms:
            if i in in_degree and k in in_degree:
                adj[i].append(k)
                in_degree[k] += 1
        order = []
        queue = [op for op in m_ops if in_degree[op] == 0]
        while queue:
            node = queue.pop(0)
            order.append(node)
            for nbr in adj[node]:
                in_degree[nbr] -= 1
                if in_degree[nbr] == 0:
                    queue.append(nbr)
        machine_order[m] = order

    return machine_order, True


# ============================================================
# SEVERITY COMPUTATION (Layer 1 - Governance)
# ============================================================

EVENT_TYPE_WEIGHTS = {
    "breakdown": 5,
    "rush_order": 4,
    "delay": 3,
    "shortage": 4,
}

THRESHOLDS = {
    "interpretation": 8,
    "execution_governance": 12,
}


def compute_severity(event, ops, job_sequences, completion, machine_criticality_map):
    """
    Compute severity score: event_type_weight + machine_criticality + due_proximity.
    due_proximity: 3 if any job on affected machine has ops completing within 20% of makespan,
                   2 if within 40%, else 1.
    """
    w1 = EVENT_TYPE_WEIGHTS.get(event.event_type, 3)
    w2 = machine_criticality_map.get(event.affected_machine, 2)

    # due_proximity: based on how close at-risk ops are to their successors completing
    makespan = max(completion.values()) if completion else 1
    at_risk_completions = [
        completion[op_id] for op_id, op in ops.items()
        if op.machine == event.affected_machine and op_id in completion
    ]
    if at_risk_completions:
        latest = max(at_risk_completions)
        proximity = latest / makespan if makespan > 0 else 0
        w3 = 3 if proximity >= 0.8 else (2 if proximity >= 0.6 else 1)
    else:
        w3 = 1

    return w1 + w2 + w3


def check_feasibility_violation(event, ops, completion):
    """Check if current schedule violates availability due to disruption window."""
    for op_id, op in ops.items():
        if op.machine == event.affected_machine and op_id in completion:
            start = completion[op_id] - op.duration
            finish = completion[op_id]
            if start < event.window_end and finish > event.window_start:
                return True
    return False


def governance_decision(event, ops, job_sequences, completion,
                        machine_criticality_map, stage):
    """
    Run governance decision for given stage.
    Returns GovernanceDecision.
    """
    severity = compute_severity(event, ops, job_sequences, completion, machine_criticality_map)
    event.severity_score = severity

    if stage == "interpretation":
        violated = check_feasibility_violation(event, ops, completion)
        if severity >= THRESHOLDS["interpretation"] or violated:
            outcome = "replan"
        else:
            outcome = "continue"
        return GovernanceDecision(
            event_id=event.event_id,
            stage=stage,
            outcome=outcome,
            severity=severity,
            violated_feasibility=violated
        )
    else:  # execution_governance
        if severity >= THRESHOLDS["execution_governance"]:
            outcome = "human_approval"
        else:
            outcome = "autonomous"
        return GovernanceDecision(
            event_id=event.event_id,
            stage=stage,
            outcome=outcome,
            severity=severity
        )


# ============================================================
# REFINEMENT STAGE (Layer 3 - Critical-Block Local Search)
# ============================================================

def find_critical_path(ops, completion):
    """Find the critical path (sequence of ops achieving makespan)."""
    if not completion:
        return []
    makespan = max(completion.values())
    critical = []
    # Trace backward from makespan op
    current = max(completion, key=completion.get)
    critical.append(current)
    return critical  # simplified; full implementation below


def get_machine_immediate_predecessors(machine_order):
    """Build immediate predecessor map on each machine."""
    imm_pred = {}
    for m, order in machine_order.items():
        for i in range(1, len(order)):
            imm_pred[order[i]] = order[i-1]
    return imm_pred


def compute_critical_path(ops, job_precs, machine_order, completion):
    """Identify all operations on the critical path."""
    if not completion:
        return []
    makespan = max(completion.values())
    job_pred_map = {k: v for v, k in job_precs}
    imm_mach_pred = get_machine_immediate_predecessors(machine_order)

    on_critical = set()
    # An operation is critical if removing slack would extend makespan
    for op_id, comp in completion.items():
        start = comp - ops[op_id].duration
        # Check if it's on the critical path
        if comp == makespan:
            on_critical.add(op_id)
        # Trace predecessors
        for pred_type, pred_map in [("job", job_pred_map), ("machine", imm_mach_pred)]:
            if op_id in pred_map:
                pred = pred_map[op_id]
                if pred in completion and completion[pred] == start:
                    on_critical.add(pred)

    return list(on_critical)


def find_critical_blocks(critical_ops, ops, machine_order):
    """
    Partition critical ops into maximal blocks of consecutive same-machine ops.
    Returns list of (machine, [op_id, ...]) blocks.
    """
    crit_set = set(critical_ops)
    blocks = []
    for m, order in machine_order.items():
        crit_on_m = [op for op in order if op in crit_set]
        if len(crit_on_m) < 2:
            continue
        # Find consecutive pairs
        order_idx = {op: i for i, op in enumerate(order)}
        i = 0
        while i < len(crit_on_m) - 1:
            block = [crit_on_m[i]]
            j = i + 1
            while j < len(crit_on_m):
                if order_idx[crit_on_m[j]] == order_idx[crit_on_m[j-1]] + 1:
                    block.append(crit_on_m[j])
                    j += 1
                else:
                    break
            if len(block) >= 2:
                blocks.append((m, block))
                i = j
            else:
                i += 1
    return blocks


def try_swap(op_a, op_b, machine, ops, job_precs, machine_order, disrupted):
    """
    Try swapping op_a and op_b (adjacent on machine).
    Returns new machine_order and completion if improving, else None.
    """
    new_order = {m: list(seq) for m, seq in machine_order.items()}
    seq = new_order[machine]
    ia = seq.index(op_a)
    ib = seq.index(op_b)
    if abs(ia - ib) != 1:
        return None, None

    seq[ia], seq[ib] = seq[ib], seq[ia]

    # Check acyclicity (simplified: check job precedence not violated by swap)
    job_pred_map = {k: v for v, k in job_precs}
    if op_a in job_pred_map and job_pred_map[op_a] == op_b:
        return None, None
    if op_b in job_pred_map and job_pred_map[op_b] == op_a:
        return None, None

    new_completion, new_makespan, feasible = decode_schedule(
        ops, job_precs, new_order, disrupted
    )
    if not feasible:
        return None, None
    return new_order, new_completion


def refinement_stage(ops, job_precs, machine_order, disrupted, max_iter=200):
    """
    Critical-block local search. Returns (refined_order, refined_completion, makespan).
    """
    completion, makespan, feasible = decode_schedule(ops, job_precs, machine_order, disrupted)
    if not feasible:
        return machine_order, completion, makespan

    current_order = {m: list(seq) for m, seq in machine_order.items()}
    current_completion = completion
    current_makespan = makespan

    for _ in range(max_iter):
        critical_ops = compute_critical_path(ops, job_precs, current_order, current_completion)
        if len(critical_ops) < 2:
            break
        blocks = find_critical_blocks(critical_ops, ops, current_order)
        if not blocks:
            break

        improved = False
        for m, block in blocks:
            for i in range(len(block) - 1):
                op_a, op_b = block[i], block[i+1]
                new_order, new_completion = try_swap(
                    op_a, op_b, m, ops, job_precs, current_order, disrupted
                )
                if new_completion is None:
                    continue
                new_makespan = max(new_completion.values()) if new_completion else 0
                if new_makespan < current_makespan:
                    current_order = new_order
                    current_completion = new_completion
                    current_makespan = new_makespan
                    improved = True
                    break
            if improved:
                break

        if not improved:
            break

    return current_order, current_completion, current_makespan


# ============================================================
# NAIVE ABSORPTION BASELINE
# ============================================================

def naive_response(ops, job_precs, machine_order, disrupted):
    """
    Passive absorption: keep existing ordering, defer affected ops past window.
    Returns (completion, makespan, feasible).
    """
    return decode_schedule(ops, job_precs, machine_order, disrupted)


# ============================================================
# MACHINE CRITICALITY MAP BUILDER
# ============================================================

def build_machine_criticality(ops, n_machines):
    """
    Assign criticality weights based on operation load on each machine.
    More operations = higher criticality (1-4 scale).
    """
    load = defaultdict(int)
    for op in ops.values():
        load[op.machine] += op.duration
    if not load:
        return {}
    max_load = max(load.values())
    criticality = {}
    for m in range(n_machines):
        l = load.get(m, 0)
        if l >= 0.75 * max_load:
            criticality[m] = 4
        elif l >= 0.5 * max_load:
            criticality[m] = 3
        elif l >= 0.25 * max_load:
            criticality[m] = 2
        else:
            criticality[m] = 1
    return criticality


# ============================================================
# OPS RESEQUENCED METRIC
# ============================================================

def count_resequenced_ops(order_before, order_after):
    """Count operations that changed position across all machines."""
    count = 0
    for m in order_before:
        if m not in order_after:
            continue
        before_pos = {op: i for i, op in enumerate(order_before[m])}
        after_pos = {op: i for i, op in enumerate(order_after[m])}
        for op in before_pos:
            if op in after_pos and before_pos[op] != after_pos[op]:
                count += 1
    return count
