"""Calibration of DKeX moneyline prices by minutes to settlement, following Moshrefi (2026) on Kalshi: fit the power-law
C(p) = p^g / (p^g + (1-p)^g) and Platt scaling C(p) = sigma(a logit p + b) to trades bucketed by time to expiry
(0-10, 10-30, 30-90, 90-240 minutes, 4-24 hours, more than a day). g = a = 1 is perfect calibration; values above one mean an
S-shape (low prices win less often than they say, high prices more), which Moshrefi reads as insurance demand in the final minutes.
Results in results_calibration.json."""
import os, json, math, duckdb, numpy as np
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKETS = [(0, 10, "0-10 min"), (10, 30, "10-30 min"), (30, 90, "30-90 min"), (90, 240, "90-240 min"), (240, 1440, "4-24 h"), (1440, 1e9, "> 1 day")]


def fit(prices, wins, weights):
    p = np.clip(np.asarray(prices), 0.005, 0.995); y = np.asarray(wins); w = np.asarray(weights)
    lg = np.log(p / (1 - p))
    def nll_power(g):
        q = p ** g[0] / (p ** g[0] + (1 - p) ** g[0]); q = np.clip(q, 1e-9, 1 - 1e-9)
        return -np.sum(w * (y * np.log(q) + (1 - y) * np.log(1 - q)))
    def nll_platt(ab):
        q = 1 / (1 + np.exp(-(ab[0] * lg + ab[1]))); q = np.clip(q, 1e-9, 1 - 1e-9)
        return -np.sum(w * (y * np.log(q) + (1 - y) * np.log(1 - q)))
    g = minimize(nll_power, [1.0], method="Nelder-Mead").x[0]
    a, b = minimize(nll_platt, [1.0, 0.0], method="Nelder-Mead").x
    return g, a, b


def main():
    con = duckdb.connect(os.path.join(HERE, "data", "dkex", "dkex.duckdb"), read_only=True)
    # aggregate by (league, minute bucket, price cent): quantity and quantity-weighted hit rate
    rows = con.execute("""SELECT c.league, date_diff('second', t.ts, c.settle_ts)/60.0 AS m, round(t.price*100) AS cent, sum(t.qty) AS q, sum(c.settle*t.qty) AS hits
        FROM trades t JOIN contracts c USING(symbol) WHERE c.mtype='WIN' AND c.period='FG' AND c.settle IN (0,1) AND c.settle_ts IS NOT NULL
        AND date_diff('second', t.ts, c.settle_ts) >= 0 GROUP BY 1,2,3""").fetchall()
    R = {}
    print(f"{'league':8s} {'bucket':12s} {'contracts':>12s} {'gamma':>7s} {'platt a':>8s} {'platt b':>8s} {'win rate at 20c':>16s} {'at 80c':>8s}")
    for league in ["ALL", "NFL", "CFB", "MLB"]:
        for lo, hi, name in BUCKETS:
            sub = [r for r in rows if (league == "ALL" or r[0] == league) and lo <= r[1] < hi]
            if sum(r[3] for r in sub) < 200000: continue
            # collapse to price cents
            agg = {}
            for _, _, cent, q, hits in sub:
                a = agg.setdefault(cent, [0, 0]); a[0] += q; a[1] += hits
            prices = [c / 100 for c in agg]; wins = [agg[c][1] / agg[c][0] for c in agg]; weights = [agg[c][0] for c in agg]
            g, a, b = fit(prices, wins, weights)
            lo20 = sum(agg[c][1] for c in agg if 15 <= c <= 25) / max(1, sum(agg[c][0] for c in agg if 15 <= c <= 25))
            hi80 = sum(agg[c][1] for c in agg if 75 <= c <= 85) / max(1, sum(agg[c][0] for c in agg if 75 <= c <= 85))
            R[f"{league}|{name}"] = dict(contracts=sum(weights), gamma=g, platt_a=a, platt_b=b, win_rate_15_25c=lo20, win_rate_75_85c=hi80)
            print(f"{league:8s} {name:12s} {sum(weights):12,.0f} {g:7.3f} {a:8.3f} {b:8.3f} {lo20:16.3f} {hi80:8.3f}")
    json.dump(R, open(os.path.join(HERE, "results_calibration.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
