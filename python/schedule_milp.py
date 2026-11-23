"""Flexible job-shop MILP for a batch of fab lots, a dispatching-rule baseline, and export for AnyLogic.

Usage: python schedule_milp.py --lots-a 3 --lots-b 3 [--solver gurobi|highs] [--time-limit 60]
Outputs: schedule.xlsx (sheets: milp, rule_<name>, lot_priority) and a printed comparison.
"""
import argparse
import itertools

import pandas as pd
import pulp

TOL = 1e-6


def load(path: str):
    tg = pd.read_excel(path, sheet_name="tool_groups")
    routes = pd.read_excel(path, sheet_name="routes")
    tools = {r.tool_group: [f"{r.tool_group}_{k + 1}" for k in range(int(r.num_tools))] for r in tg.itertuples()}
    return routes, tools


def make_ops(routes: pd.DataFrame, n_a: int, n_b: int) -> pd.DataFrame:
    lots = [(f"A{i + 1}", "A") for i in range(n_a)] + [(f"B{i + 1}", "B") for i in range(n_b)]
    rows = []
    for lot, prod in lots:
        for r in routes[routes["product"] == prod].itertuples():
            rows.append((lot, prod, r.step, r.tool_group, float(r.proc_time_h)))
    return pd.DataFrame(rows, columns=["lot", "product", "step", "tool_group", "proc_time_h"])


def solve_milp(ops: pd.DataFrame, tools: dict, solver: str, time_limit: int) -> tuple[pd.DataFrame, str]:
    m = pulp.LpProblem("fab_fjsp", pulp.LpMinimize)
    O = list(ops.index)
    p = ops["proc_time_h"].to_dict()
    big_m = float(ops["proc_time_h"].sum())
    s = {o: pulp.LpVariable(f"s_{o}", lowBound=0) for o in O}
    x = {(o, k): pulp.LpVariable(f"x_{o}_{k}", cat="Binary") for o in O for k in tools[ops.at[o, "tool_group"]]}
    cmax = pulp.LpVariable("Cmax", lowBound=0)
    m += cmax
    for o in O:
        m += pulp.lpSum(x[o, k] for k in tools[ops.at[o, "tool_group"]]) == 1
        m += cmax >= s[o] + p[o]
    for _, g in ops.groupby("lot"):
        idx = list(g.sort_values("step").index)
        for a, b in zip(idx, idx[1:]):
            m += s[b] >= s[a] + p[a]
    for grp, g in ops.groupby("tool_group"):
        for a, b in itertools.combinations(list(g.index), 2):
            if ops.at[a, "lot"] == ops.at[b, "lot"]:
                continue
            for k in tools[grp]:
                z = pulp.LpVariable(f"z_{a}_{b}_{k}", cat="Binary")
                both = 2 - x[a, k] - x[b, k]
                m += s[b] >= s[a] + p[a] - big_m * (1 - z) - big_m * both
                m += s[a] >= s[b] + p[b] - big_m * z - big_m * both
    cmd = pulp.GUROBI(msg=False, timeLimit=time_limit) if solver == "gurobi" else pulp.HiGHS(msg=False, timeLimit=time_limit)
    m.solve(cmd)
    print(f"MILP size: {len(m.variables())} variables, {len(m.constraints)} constraints")
    status = {pulp.LpSolutionOptimal: "optimal", pulp.LpSolutionIntegerFeasible: "feasible, time limit hit"}.get(m.sol_status)
    if status is None:
        raise RuntimeError(f"MILP failed: {pulp.LpStatus[m.status]}")
    out = ops.copy()
    out["start_h"] = [round(s[o].value(), 4) for o in O]
    out["tool"] = [next(k for k in tools[ops.at[o, "tool_group"]] if x[o, k].value() > 0.5) for o in O]
    out["end_h"] = out["start_h"] + out["proc_time_h"]
    return out, status


def dispatch(ops: pd.DataFrame, tools: dict, rule: str) -> pd.DataFrame:
    """Non-delay list scheduling: whenever a tool frees up, start the waiting operation preferred by the rule."""
    remaining = ops.groupby("lot")["proc_time_h"].sum().to_dict()
    next_step = {lot: 1 for lot in ops["lot"].unique()}
    ready_at = {lot: 0.0 for lot in next_step}
    free_at = {k: 0.0 for ks in tools.values() for k in ks}
    order = {lot: i for i, lot in enumerate(next_step)}
    done = []
    while len(done) < len(ops):
        cands = []
        for lot, st in next_step.items():
            row = ops[(ops["lot"] == lot) & (ops["step"] == st)]
            if row.empty:
                continue
            o = row.index[0]
            k = min(tools[ops.at[o, "tool_group"]], key=lambda t: free_at[t])
            start = max(ready_at[lot], free_at[k])
            key = {"FIFO": order[lot], "SPT": ops.at[o, "proc_time_h"], "LWKR": remaining[lot],
                   "MWKR": -remaining[lot]}[rule]
            cands.append((start, key, o, k, lot))
        start, _, o, k, lot = min(cands)
        end = start + ops.at[o, "proc_time_h"]
        free_at[k], ready_at[lot] = end, end
        remaining[lot] -= ops.at[o, "proc_time_h"]
        next_step[lot] += 1
        done.append((o, k, start, end))
    out = ops.copy()
    for o, k, start, end in done:
        out.loc[o, ["tool", "start_h", "end_h"]] = [k, start, end]
    return out


def check(sched: pd.DataFrame) -> float:
    """Independent feasibility check; returns makespan."""
    for _, g in sched.groupby("lot"):
        g = g.sort_values("step")
        assert (g["start_h"].values[1:] >= g["end_h"].values[:-1] - TOL).all(), "route order violated"
    for _, g in sched.groupby("tool"):
        g = g.sort_values("start_h")
        assert (g["start_h"].values[1:] >= g["end_h"].values[:-1] - TOL).all(), "tool double-booked"
    return float(sched["end_h"].max())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="fab_data.xlsx")
    ap.add_argument("--lots-a", type=int, default=3)
    ap.add_argument("--lots-b", type=int, default=3)
    ap.add_argument("--solver", choices=["highs", "gurobi"], default="highs")
    ap.add_argument("--time-limit", type=int, default=60)
    a = ap.parse_args()
    routes, tools = load(a.data)
    ops = make_ops(routes, a.lots_a, a.lots_b)
    results = {}
    milp, status = solve_milp(ops, tools, a.solver, a.time_limit)
    results[f"MILP ({status})"] = check(milp)
    sheets = {"milp": milp}
    for rule in ("FIFO", "SPT", "LWKR", "MWKR"):
        sch = dispatch(ops, tools, rule)
        results[rule] = check(sch)
        sheets[f"rule_{rule}"] = sch
    lb = max(ops.groupby("lot")["proc_time_h"].sum().max(),
             max(ops[ops["tool_group"] == g]["proc_time_h"].sum() / len(ks) for g, ks in tools.items()))
    assert all(v >= lb - TOL for v in results.values()), "makespan below lower bound: model bug"
    first = milp.sort_values("start_h").drop_duplicates("lot")[["lot", "product", "start_h"]]
    sheets["lot_priority"] = first.assign(priority=range(len(first), 0, -1))
    with pd.ExcelWriter("schedule.xlsx") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
    print(f"lower bound on makespan: {lb:.2f} h")
    for k, v in results.items():
        print(f"{k:>16}: makespan {v:.2f} h")
