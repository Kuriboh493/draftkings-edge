"""Backtest of the DKeX strategies in strategies.py over the whole tape, with weekly P&L, a date split (rules looked at on
June 11 - Sep 13, judged on Sep 14 - Oct 1, the NFL-heavy part) and a few parameter variants. Fees are a parameter: DraftKings
says the vig is in the spread rather than a stated fee; the tables show no fee and DKeX's schedule (maker $0.0025/contract; taker 1-2c). Results in results_backtest.json."""
import os, sys, json, math, datetime as dt, statistics as S, collections, duckdb
from strategies import STRATEGIES

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = 20260914
FEES = [0.0, "dkex"]


SETTLE = {}


def settle_fills(con, fills):
    if not SETTLE:
        SETTLE.update(con.execute("SELECT symbol, settle FROM contracts WHERE settle IS NOT NULL").fetchall())
    for f in fills:
        s = SETTLE.get(f["symbol"]); f["settle"] = s
        if s is None: f["pnl"] = None; continue
        f["pnl"] = (f["price"] - s) * f["qty"] if f["side"] == "sell" else (s - f["price"]) * f["qty"]
    return fills


def dkex_fee(f):
    """DKeX schedule: maker $0.0025 per contract; taker 1c below 20c or above 96c, 2c otherwise. Resting strategies are makers; last_minutes takes."""
    if f["strategy"] == "last_minutes":
        return 0.01 if f["price"] < 0.20 or f["price"] > 0.96 else 0.02
    return 0.0025


def summarise(fills, fee):
    out = {}
    byday = collections.defaultdict(lambda: [0.0, 0, 0])
    for f in fills:
        if f.get("pnl") is None: continue
        fee_c = dkex_fee(f) if fee == "dkex" else fee
        d = f["ts"].date().isoformat(); byday[d][0] += f["pnl"] - fee_c * f["qty"]; byday[d][1] += f["qty"]; byday[d][2] += 1
    wk = collections.defaultdict(lambda: [0.0, 0])
    for d, (p, q, n) in byday.items():
        y, w, _ = dt.date.fromisoformat(d).isocalendar(); wk[f"{y}-W{w:02d}"][0] += p; wk[f"{y}-W{w:02d}"][1] += q
    ws = [v[0] for v in wk.values()]
    tot = sum(ws); q = sum(v[1] for v in wk.values())
    out.update(fills=sum(1 for f in fills if f.get("pnl") is not None), contracts=q, pnl=tot, cents_per_contract=100 * tot / q if q else None,
               weeks=len(ws), weeks_positive=sum(w > 0 for w in ws), weekly_t=(S.mean(ws) / (S.stdev(ws) / math.sqrt(len(ws))) if len(ws) > 2 and S.stdev(ws) > 0 else None),
               worst_week=min(ws) if ws else None, best_week=max(ws) if ws else None, days=len(byday), contracts_per_day=q / len(byday) if byday else 0, weekly=dict(sorted(wk.items())))
    return out


def run(con, name, b0, b1, **kw):
    fn = STRATEGIES[name]; state = {}
    return settle_fills(con, fn(con, b0, b1, state, **kw))


def main():
    con = duckdb.connect(os.path.join(HERE, "data", "dkex", "dkex.duckdb"), read_only=True)
    b0, b1 = con.execute("select min(bdate), max(bdate) from trades").fetchone()
    variants = [("combo_desk", {}), ("combo_followon", {}), ("combo_followon", {"improve": 0.02}), ("combo_followon", {"improve": 0.0}),
                ("moneyline_maker", {}), ("moneyline_maker", {"k": 0.02}), ("moneyline_maker", {"k": 0.005}), ("moneyline_maker", {"min_hours": 0.0}),
                ("last_minutes", {}), ("last_minutes", {"max_min": 30}), ("last_minutes", {"lo": 0.05, "hi": 0.25})]
    R = {}
    for name, kw in variants:
        fills = run(con, name, b0, b1, **kw)
        label = name + ("" if not kw else " " + json.dumps(kw, sort_keys=True))
        R[label] = {}
        for fee in FEES:
            allp = summarise(fills, fee); tr = summarise([f for f in fills if int(f["ts"].strftime("%Y%m%d")) < SPLIT], fee); te = summarise([f for f in fills if int(f["ts"].strftime("%Y%m%d")) >= SPLIT], fee)
            R[label][f"fee_{fee}"] = dict(all=allp, train=tr, test=te)
            if fee == 0.0:
                print(f"\n== {label}")
                for part, s in (("all", allp), ("train <Sep14", tr), ("test >=Sep14", te)):
                    if s["contracts"]:
                        print(f"  {part:13s} fills {s['fills']:8d}  contracts {s['contracts']:12,.0f}  P&L ${s['pnl']:12,.0f}  {s['cents_per_contract']:6.2f}c/contract  weeks {s['weeks_positive']}/{s['weeks']}  t {s['weekly_t'] if s['weekly_t'] is None else round(s['weekly_t'],2)}  worst wk ${s['worst_week']:,.0f}  per day {s['contracts_per_day']:,.0f} contracts")
            else:
                print(f"  after DKeX fees: all {allp['cents_per_contract'] if allp['contracts'] else float('nan'):.2f}c/contract (${allp['pnl']:,.0f}), test {te['cents_per_contract'] if te['contracts'] else float('nan'):.2f}c (${te['pnl']:,.0f})")
        # extra cuts for the main combo strategy
        if name == "combo_followon" and not kw:
            legs = dict(con.execute("SELECT symbol, legs FROM contracts WHERE symbol LIKE 'COMBO-%' AND legs IS NOT NULL").fetchall())
            byleg = collections.defaultdict(lambda: [0.0, 0])
            for f in fills:
                if f.get("pnl") is None: continue
                L = legs.get(f["symbol"]); byleg[L][0] += f["pnl"]; byleg[L][1] += f["qty"]
            R[label]["by_legs"] = {str(k): dict(pnl=v[0], contracts=v[1], cents=100 * v[0] / v[1]) for k, v in sorted(byleg.items()) if v[1]}
            print("  by legs: " + "  ".join(f"{k}: {100*v[0]/v[1]:.2f}c on {v[1]/1e6:.1f}M" for k, v in sorted(byleg.items()) if v[1]))
            unsettled = sum(f["qty"] for f in fills if f.get("pnl") is None)
            print(f"  open (unsettled) contracts at end of tape: {unsettled:,}")
        if name == "moneyline_maker" and not kw:
            sells = [f for f in fills if f["side"] == "sell" and f.get("pnl") is not None]; buys = [f for f in fills if f["side"] == "buy" and f.get("pnl") is not None]
            print(f"  sells {sum(f['qty'] for f in sells):,} contracts {100*sum(f['pnl'] for f in sells)/max(1,sum(f['qty'] for f in sells)):.2f}c; buys {sum(f['qty'] for f in buys):,} contracts {100*sum(f['pnl'] for f in buys)/max(1,sum(f['qty'] for f in buys)):.2f}c")
            R[label]["sides"] = dict(sell_contracts=sum(f['qty'] for f in sells), sell_cents=100*sum(f['pnl'] for f in sells)/max(1,sum(f['qty'] for f in sells)), buy_contracts=sum(f['qty'] for f in buys), buy_cents=100*sum(f['pnl'] for f in buys)/max(1,sum(f['qty'] for f in buys)))
    json.dump(R, open(os.path.join(HERE, "results_backtest.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
