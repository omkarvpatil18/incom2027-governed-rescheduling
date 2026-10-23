# Governed Autonomy in Production Rescheduling: A Comparative Study Across Disruption Types

**INCOM 2027 Conference Paper**  
19th IFAC Symposium on Information, Control and Optimisation in Manufacturing  
Nantes, France, June 28–30, 2027

**Authors:** Omkar Vishwas Patil, Omid Fatahi Valilai  
Constructor University Bremen, Germany

---

## Abstract

Industry 6.0 demands that autonomous production decisions be accountable: every rescheduling action must be traceable and the boundary between machine authority and human approval must be explicit. This paper presents a governed autonomy framework for job shop rescheduling and compares it against three ungoverned baselines (always-replan, random-replan, threshold-without-execution-governance) across four disruption types on Lawrence benchmark instances from the OR-Library (300–450 operations, 320 experimental cells). The governed framework achieves 100% governance accuracy, routes breakdown events to human approval in 80% of cases, and achieves makespan improvements of up to +6.2% relative to naive passive absorption.

---

## Repository Structure

```
incom2027-repo/
│
├── experiments/
│   ├── framework.py                # Three-layer governed framework
│   ├── framework_fast.py           # Greedy baseline + fast utilities
│   ├── run_experiments.py          # Main experiment runner
│   └── requirements.txt            # Python dependencies
│
├── instances/
│   ├── la_instances_real.py        # LA21, LA36 from OR-Library (exact data)
│   └── dmu_instances.py            # DMU01, DMU06 (synthetic approximation)
│
├── results/
│   ├── incom2027_la_results.csv    # Full results: 320 experimental cells
│   └── incom2027_la_results.json   # Same results in JSON format
│
│
└── README.md
```

---

## Benchmark Instances

Experiments use two instances from the Lawrence (LA) benchmark family, obtained directly from the OR-Library (Beasley 1990):

| Instance | Jobs | Machines | Operations | Published Optimum | Source |
|----------|------|----------|------------|-------------------|--------|
| LA21     | 15   | 10       | 150        | 1046              | Lawrence (1984), setb1 |
| LA36     | 15   | 15       | 225        | 1268              | Lawrence (1984), seti1 |

Processing times are in the original published range [7, 99].

---

## Disruption Scenario Parameters

Disruption scenarios are generated using published industrial parameters:

| Disruption Type | Frequency | Window Start | Window Duration | Source |
|-----------------|-----------|--------------|-----------------|--------|
| Breakdown       | 35%       | U[10%, 70%] of makespan | U[8%, 25%] | Vieira et al. (2003) |
| Delay           | 30%       | U[10%, 70%] | U[8%, 25%]  | Aytug et al. (2005) |
| Rush Order      | 25%       | U[10%, 60%] | U[5%, 15%]  | Sabuncuoglu & Bayiz (2000) |
| Shortage        | 10%       | U[10%, 70%] | U[8%, 25%]  | Aytug et al. (2005) |

Seed: 99 | Scenarios per type per instance: 10 | Total cells: 320

---

## Framework Architecture

The governed framework comprises three layers:

```
Event detected
      │
      ▼
┌─────────────────────────────────────┐
│  Layer 1: Declarative Governance    │
│  Severity Σ(e,S) = α + β + γ       │
│  Γ₁: replan or continue?            │
└─────────────────────────────────────┘
      │ (if replan)
      ▼
┌─────────────────────────────────────┐
│  Layer 2: Feasibility Restoration   │
│  find S' s.t. job prec + avail      │
└─────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  Layer 3: Heuristic Refinement      │
│  S* = argmin Cmax over N(S')        │
└─────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  Layer 1 (again): Exec Governance   │
│  Γ₂: autonomous or human_approval?  │
└─────────────────────────────────────┘
```

Policy thresholds: τ₁ = 8 (interpretation), τ₂ = 12 (execution-governance)

---

## Strategies Compared

| Strategy | Description |
|----------|-------------|
| **GOV** (Governed) | Full pipeline Π: applies Γ₁ and Γ₂ |
| **AR** (Always-Replan) | Replans every event, no governance |
| **RR** (Random-Replan) | Replans with p=0.5, no governance |
| **TO** (Threshold-Only) | Applies τ₁ but omits Γ₂ (always autonomous) |

---

## Key Results

| Metric | GOV | AR | RR | TO |
|--------|-----|----|----|-----|
| Breakdown Δ*C*max | +4.6% | +4.5% | +3.3% | +4.2% |
| Rush Order Δ*C*max | +4.1% | +4.3% | +1.9% | +3.7% |
| Delay Δ*C*max | +5.8% | +5.4% | +2.2% | +4.4% |
| Shortage Δ*C*max | +4.3% | +3.8% | +1.9% | +4.8% |
| Breakdown escalation | **80%** | 0% | 0% | 0% |
| Governance accuracy | **100%** | N/A | N/A | N/A |
| Infeasibility rate | **0%** | 0% | 0% | 0% |

---

## Requirements

```
Python >= 3.9
clingo >= 5.6     # Answer Set Programming solver
```

Install dependencies:
```bash
pip install clingo
```

---

## Reproducing the Experiments

```bash
cd experiments/
python run_experiments.py
```

Results are written to `results/incom2027_la_results.csv` and `.json`.

---

## Funding

This work is supported by the European Union's Horizon Europe programme through the project SORT4CIRC (Intelligent Textile Sorting for enabling CIRCularity), Grant Agreement No. 101181988.

---

## Citation

```bibtex
@inproceedings{patil2027governed,
  author    = {Patil, Omkar Vishwas and Fatahi Valilai, Omid},
  title     = {Governed Autonomy in Production Rescheduling:
               A Comparative Study Across Disruption Types},
  booktitle = {Proceedings of the 19th IFAC Symposium on
               Information, Control and Optimisation in Manufacturing},
  year      = {2027},
  address   = {Nantes, France}
}
```

---

## References

- Lawrence, S. (1984). Resource constrained project scheduling. Carnegie-Mellon University.
- Beasley, J.E. (1990). OR-Library. Journal of the Operational Research Society, 41(11), 1069–1072.
- Vieira, G.E., Herrmann, J.W., Lin, E. (2003). Rescheduling manufacturing systems. Journal of Scheduling, 6(1), 39–62.
- Aytug, H. et al. (2005). Executing production schedules in the face of uncertainties. EJOR, 161(1), 86–110.
- Sabuncuoglu, I., Bayiz, M. (2000). Analysis of reactive scheduling problems. EJOR, 126(3), 567–586.
- Nowicki, E., Smutnicki, C. (1996). A fast taboo search algorithm for the job shop problem. Management Science, 42(6), 797–813.
- Ivanov, D. (2026). The ecosystem age. International Journal of Production Research, 64(1), 1–20.
