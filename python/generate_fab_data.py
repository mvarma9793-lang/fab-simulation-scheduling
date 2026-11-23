"""Create a small synthetic semiconductor fab dataset (fab_data.xlsx) for the AnyLogic model and the MILP.

Units: time in hours, rates in lots per day.
"""
import pandas as pd

TOOL_GROUPS = pd.DataFrame([
    # group,  tools, mtbf_h, mttr_h, batch_size
    ("DEPO",  2, 120.0, 4.0, 1),
    ("LITHO", 2, 80.0, 3.0, 1),
    ("ETCH",  2, 100.0, 4.0, 1),
    ("IMPL",  1, 150.0, 5.0, 1),
    ("CMP",   1, 120.0, 3.0, 1),
    ("METRO", 1, 200.0, 2.0, 1),
], columns=["tool_group", "num_tools", "mtbf_h", "mttr_h", "batch_size"])

ROUTES = {
    "A": [("DEPO", 2.0), ("LITHO", 1.5), ("ETCH", 1.8), ("IMPL", 1.2), ("DEPO", 2.0),
          ("LITHO", 1.5), ("ETCH", 1.8), ("CMP", 1.0), ("METRO", 0.5)],
    "B": [("DEPO", 2.2), ("LITHO", 1.7), ("ETCH", 1.5), ("CMP", 1.1), ("LITHO", 1.7),
          ("ETCH", 1.5), ("METRO", 0.6)],
}
PRODUCTS = pd.DataFrame([("A", 6.0, 0.6), ("B", 5.0, 0.4)],
                        columns=["product", "start_rate_lots_per_day", "mix"])


def routes_table() -> pd.DataFrame:
    rows = [(p, i + 1, g, t) for p, steps in ROUTES.items() for i, (g, t) in enumerate(steps)]
    return pd.DataFrame(rows, columns=["product", "step", "tool_group", "proc_time_h"])


def utilization(routes: pd.DataFrame) -> pd.DataFrame:
    """Offered load per tool group: (lots/h * work per lot) / (tools * availability)."""
    rate = PRODUCTS.set_index("product")["start_rate_lots_per_day"] / 24.0
    work = routes.assign(load=routes["product"].map(rate) * routes["proc_time_h"]).groupby("tool_group")["load"].sum()
    tg = TOOL_GROUPS.set_index("tool_group")
    avail = tg["mtbf_h"] / (tg["mtbf_h"] + tg["mttr_h"])
    return (work / (tg["num_tools"] * avail)).rename("utilization").reset_index()


if __name__ == "__main__":
    routes = routes_table()
    util = utilization(routes)
    assert (util["utilization"] < 1.0).all(), "fab is overloaded: reduce start rates"
    with pd.ExcelWriter("fab_data.xlsx") as xw:
        TOOL_GROUPS.to_excel(xw, sheet_name="tool_groups", index=False)
        routes.to_excel(xw, sheet_name="routes", index=False)
        PRODUCTS.to_excel(xw, sheet_name="products", index=False)
        util.to_excel(xw, sheet_name="expected_utilization", index=False)
    print(util.sort_values("utilization", ascending=False).to_string(index=False))
