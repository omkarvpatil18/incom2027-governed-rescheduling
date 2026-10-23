"""
Fast framework additions: greedy baseline + bounded ASP for rescheduling.
Replaces ASP for baseline; keeps ASP for per-disruption feasibility restoration.
"""
from collections import defaultdict
from framework import decode_schedule, refinement_stage, Operation
import clingo, time

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

def greedy_baseline(ops, job_seqs, job_precs, seed_shuffle=None):
    """SPT-dispatching greedy schedule. Fast O(n^2), always feasible."""
    job_pred_map = {k: v for v, k in job_precs}
    machine_order = defaultdict(list)
    remaining = set(ops.keys())
    scheduled = set()
    op_list = list(ops.keys())
    if seed_shuffle:
        seed_shuffle.shuffle(op_list)

    for _ in range(len(ops) * len(ops)):
        ready = [op_id for op_id in op_list
                 if op_id in remaining and
                 (op_id not in job_pred_map or job_pred_map[op_id] in scheduled)]
        if not ready:
            break
        done_any = False
        machines_done = set()
        for op_id in sorted(ready, key=lambda x: ops[x].duration):
            m = ops[op_id].machine
            if m not in machines_done:
                machine_order[m].append(op_id)
                scheduled.add(op_id)
                remaining.discard(op_id)
                machines_done.add(m)
                done_any = True
        if not done_any:
            break
    return dict(machine_order)


def solve_asp_bounded(ops, job_precs, disrupted, timeout_sec=15,
                      rush_ops=None, rush_precs=None):
    """
    Run ASP scheduling with a hard timeout.
    Falls back to greedy if timeout exceeded.
    """
    all_ops = dict(ops)
    all_precs = list(job_precs)
    if rush_ops:
        all_ops.update(rush_ops)
    if rush_precs:
        all_precs.extend(rush_precs)

    op_ids = sorted(all_ops.keys())
    op_idx = {op_id: i for i, op_id in enumerate(op_ids)}

    facts = []
    for op_id, op in all_ops.items():
        facts.append(f"operation({op_idx[op_id]},{op.job_id.replace('-','_')},{op.machine},{op.duration}).")
    for pred_id, succ_id in all_precs:
        if pred_id in op_idx and succ_id in op_idx:
            facts.append(f"precedes_job({op_idx[pred_id]},{op_idx[succ_id]}).")

    program = "\n".join(facts) + "\n" + ASP_SCHEDULING

    ctl = clingo.Control(["--warn=none", f"--time-limit={timeout_sec}"])
    ctl.add("base", [], program)
    ctl.ground([("base", [])])

    machine_prec_atoms = []
    sat = False
    t0 = time.time()

    with ctl.solve(yield_=True) as handle:
        for model in handle:
            if time.time() - t0 > timeout_sec:
                break
            sat = True
            for atom in model.symbols(shown=True):
                if atom.name == "machine_prec":
                    i = atom.arguments[0].number
                    k = atom.arguments[1].number
                    machine_prec_atoms.append((op_ids[i], op_ids[k]))
            break

    if not sat:
        # Fallback to greedy
        from framework import compute_job_precedences
        import random
        rng = random.Random(42)
        order = greedy_baseline(all_ops, {}, all_precs, rng)
        comp, ms, feasible = decode_schedule(all_ops, all_precs, order, disrupted)
        return order if feasible else None, feasible

    # Reconstruct machine_order
    in_deg = defaultdict(int)
    adj = defaultdict(list)
    for i, k in machine_prec_atoms:
        adj[i].append(k)
        in_deg[k] += 1
    machine_ops = defaultdict(list)
    for op_id in all_ops:
        machine_ops[all_ops[op_id].machine].append(op_id)

    machine_order = {}
    for m, m_ops in machine_ops.items():
        local_in = {op: 0 for op in m_ops}
        local_adj = defaultdict(list)
        for i, k in machine_prec_atoms:
            if i in local_in and k in local_in:
                local_adj[i].append(k)
                local_in[k] += 1
        order = []
        queue = [op for op in m_ops if local_in[op] == 0]
        while queue:
            node = queue.pop(0)
            order.append(node)
            for nb in local_adj[node]:
                local_in[nb] -= 1
                if local_in[nb] == 0:
                    queue.append(nb)
        machine_order[m] = order if len(order) == len(m_ops) else m_ops

    return machine_order, True
