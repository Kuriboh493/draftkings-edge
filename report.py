"""Summarise the paper trader: fills, open and settled positions, P&L after fees, by strategy and by day."""
import os, sqlite3, collections, duckdb

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    st = sqlite3.connect(os.path.join(HERE, "data", "paper", "paper.db"))
    con = duckdb.connect(os.path.join(HERE, "data", "dkex", "dkex.duckdb"), read_only=True)
    fills = st.execute("SELECT strategy, symbol, ts, bdate, side, price, qty, fee FROM fills").fetchall()
    print("processed dates:", [r[0] for r in st.execute("SELECT bdate FROM processed ORDER BY 1")])
    if not fills: print("no fills yet"); return
    syms = list({f[1] for f in fills})
    con.execute("CREATE TEMP TABLE s(symbol VARCHAR)"); con.executemany("INSERT INTO s VALUES (?)", [(x,) for x in syms])
    settle = dict(con.execute("SELECT c.symbol, c.settle FROM contracts c JOIN s USING(symbol) WHERE c.settle IS NOT NULL").fetchall())
    agg = collections.defaultdict(lambda: dict(fills=0, contracts=0, open_contracts=0, pnl=0.0, fees=0.0, settled_contracts=0, days=set()))
    for strat, sym, ts, b, side, price, qty, fee in fills:
        a = agg[strat]; a["fills"] += 1; a["contracts"] += qty; a["fees"] += fee * qty; a["days"].add(b)
        if sym in settle:
            s = settle[sym]; a["settled_contracts"] += qty; a["pnl"] += (price - s) * qty if side == "sell" else (s - price) * qty
        else:
            a["open_contracts"] += qty
    for strat, a in agg.items():
        print(f"{strat:16s} days {len(a['days']):3d} fills {a['fills']:7d} contracts {a['contracts']:10,d} open {a['open_contracts']:9,d} settled {a['settled_contracts']:10,d} "
              f"P&L ${a['pnl']:10,.0f} fees ${a['fees']:8,.0f} net ${a['pnl']-a['fees']:10,.0f}" + (f" = {100*(a['pnl']-a['fees'])/a['settled_contracts']:.2f}c/settled contract" if a['settled_contracts'] else ""))
    byday = collections.defaultdict(lambda: collections.defaultdict(float))
    for strat, sym, ts, b, side, price, qty, fee in fills:
        if sym in settle: byday[b][strat] += ((price - settle[sym]) if side == "sell" else (settle[sym] - price)) * qty - fee * qty
    for b in sorted(byday): print(b, " ".join(f"{k} ${v:,.0f}" for k, v in sorted(byday[b].items())))


if __name__ == "__main__":
    main()
