"""Same-game dependence and same-game combo pricing, after Davis, Dawson & Krieger (2018), who found that favourites covering
the spread and the game going over the total are positively correlated in college football and that books price such parlays
too conservatively. Here: (1) realized joint frequencies of 'favourite covers' and 'over' from DKeX's own settled spread and
total contracts in the same event (main lines = most traded); (2) two-leg same-game spread + total combos priced against
the product of their contemporaneous leg prices, versus the 1.04 cross-game two-leg markup. Results in results_correlation.json."""
import os, json, duckdb, collections, math

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    con = duckdb.connect(os.path.join(HERE, "data", "dkex", "dkex.duckdb"), read_only=True)
    # main spread and main total per event: the most traded line
    con.execute("""CREATE TEMP TABLE mainlines AS
    WITH s AS (SELECT split_part(symbol,'-',4) AS event, league, symbol, name, settle, settle_ts, total_vol,
                      CASE WHEN mtype='MOVY' THEN 'spread' WHEN mtype IN ('TPTS','TRUNS') THEN 'total' END AS kind,
                      try_cast(regexp_extract(name, '(-?[0-9]+\\.5)$', 1) AS DOUBLE) AS line
               FROM contracts WHERE period='FG' AND league IN ('NFL','CFB','MLB') AND mtype IN ('MOVY','TPTS','TRUNS') AND settle IN (0,1) AND total_vol > 0)
    SELECT * FROM s WHERE kind IS NOT NULL QUALIFY row_number() OVER (PARTITION BY event, kind ORDER BY total_vol DESC) = 1""")
    # favourite = the spread contract whose line is negative (team gives points); its settle=1 means the favourite covered
    rows = con.execute("""SELECT sp.league, sp.event, sp.line AS spread_line, sp.settle AS fav_covers, tt.settle AS over, sp.total_vol + tt.total_vol AS vol
        FROM mainlines sp JOIN mainlines tt ON tt.event = sp.event AND tt.kind='total' WHERE sp.kind='spread' AND sp.line < 0""").fetchall()
    R = {}
    print("Realized dependence between the favourite covering and the game going over, DKeX main lines")
    print(f"{'league':6s} {'events':>7s} {'P(fav covers)':>14s} {'P(over)':>8s} {'P(both)':>8s} {'ratio':>6s} {'phi':>6s} {'P(dog covers & under)':>22s} {'ratio':>6s}")
    for league in ["ALL", "NFL", "CFB", "MLB"]:
        sub = [r for r in rows if league == "ALL" or r[0] == league]
        n = len(sub)
        if n < 30: continue
        pf = sum(r[3] for r in sub) / n; po = sum(r[4] for r in sub) / n; pb = sum(r[3] * r[4] for r in sub) / n
        pdu = sum((1 - r[3]) * (1 - r[4]) for r in sub) / n
        phi = (pb - pf * po) / math.sqrt(pf * (1 - pf) * po * (1 - po)) if 0 < pf < 1 and 0 < po < 1 else float('nan')
        R[league] = dict(events=n, p_fav_covers=pf, p_over=po, p_both=pb, ratio=pb / (pf * po), phi=phi, p_dog_under=pdu, ratio_dog_under=pdu / ((1 - pf) * (1 - po)))
        print(f"{league:6s} {n:7d} {pf:14.3f} {po:8.3f} {pb:8.3f} {pb/(pf*po):6.3f} {phi:6.3f} {pdu:22.3f} {pdu/((1-pf)*(1-po)):6.3f}")
        # by spread size: big favourites
        for lo, hi, nm in [(-100, -10, "fav by 10+"), (-10, -3.5, "fav by 3.5-10"), (-3.5, 0, "fav by < 3.5")]:
            s2 = [r for r in sub if lo <= r[2] < hi]
            if len(s2) >= 30:
                pf2 = sum(r[3] for r in s2) / len(s2); po2 = sum(r[4] for r in s2) / len(s2); pb2 = sum(r[3] * r[4] for r in s2) / len(s2)
                R[f"{league}|{nm}"] = dict(events=len(s2), p_fav_covers=pf2, p_over=po2, p_both=pb2, ratio=pb2 / (pf2 * po2))
                print(f"   {nm:14s} n={len(s2):4d} P(fav)={pf2:.3f} P(over)={po2:.3f} P(both)={pb2:.3f} ratio={pb2/(pf2*po2):.3f}")
    # (2) two-leg same-game combos: 'Yes - TEAM -N.5 - Spread / Yes - Over M.5 - Total Points' in either order
    con.execute(r"""CREATE TEMP TABLE sg AS
    SELECT symbol AS combo, settle, settle_ts,
           regexp_extract(name, 'Yes - ([^/]+ -[0-9]+\.5) - Spread', 1) AS spread_name,
           try_cast(regexp_extract(name, 'Yes - Over ([0-9]+\.5) - Total (Points|Runs)', 1) AS DOUBLE) AS total_line
    FROM contracts WHERE symbol LIKE 'COMBO-%' AND settle IN (0,1) AND legs = 2 AND name LIKE '%- Spread%' AND name LIKE '%Over %Total%' AND name NOT LIKE '%No - %'""")
    con.execute("""CREATE TEMP TABLE sgmap AS
    SELECT sg.combo, sg.settle AS combo_settle, sp.symbol AS spread_symbol, sp.event, sp.league, tt.symbol AS total_symbol, sp.settle_ts AS game_end
    FROM sg JOIN (SELECT symbol, name, settle_ts, split_part(symbol,'-',4) AS event, league FROM contracts WHERE mtype='MOVY' AND period='FG' AND settle IN (0,1)) sp ON sp.name = sg.spread_name
    JOIN (SELECT symbol, split_part(symbol,'-',4) AS event, try_cast(regexp_extract(name, '([0-9]+\\.5)$', 1) AS DOUBLE) AS line FROM contracts WHERE mtype IN ('TPTS','TRUNS') AND period='FG' AND settle IN (0,1)) tt
      ON tt.event = sp.event AND tt.line = sg.total_line""")
    n = con.execute("select count(*), count(distinct combo) from sgmap").fetchall(); print("\nsame-game spread+total 2-leg combos mapped:", n)
    con.execute("""CREATE TEMP TABLE sgt AS
    SELECT t.symbol AS combo, t.ts, t.price, t.qty, m.combo_settle, m.league, m.spread_symbol, m.total_symbol, m.game_end,
           date_diff('second', t.ts, m.game_end)/60.0 AS tte_min FROM trades t JOIN sgmap m ON m.combo = t.symbol
    QUALIFY row_number() OVER (PARTITION BY t.symbol, t.ts, t.price, t.qty ORDER BY m.spread_symbol) = 1""")
    con.execute("""CREATE TEMP TABLE lt AS SELECT symbol, ts, price FROM trades WHERE symbol IN (SELECT spread_symbol FROM sgmap UNION SELECT total_symbol FROM sgmap)""")
    con.execute("""CREATE TEMP TABLE priced AS
    SELECT s.*, a.price AS p_spread, b.price AS p_total, greatest(date_diff('second', a.ts, s.ts), date_diff('second', b.ts, s.ts)) AS stale_s
    FROM sgt s ASOF JOIN lt a ON a.symbol = s.spread_symbol AND a.ts <= s.ts ASOF JOIN lt b ON b.symbol = s.total_symbol AND b.ts <= s.ts""")
    q = """SELECT {grp} AS grp, count(*) AS trades, sum(qty) AS contracts, round(sum(price*qty)/sum(qty),4) AS combo_price, round(sum(p_spread*p_total*qty)/sum(qty),4) AS product,
      round(sum(price*qty)/sum(p_spread*p_total*qty),3) AS R_qty_weighted, round(median(price/(p_spread*p_total)),3) AS R_median,
      round(sum(combo_settle*qty)/sum(qty),4) AS realized_hit, round(100*(sum(combo_settle*qty)-sum(price*qty))/sum(price*qty),1) AS realized_return_pct
      FROM priced WHERE stale_s <= 21600 {where} GROUP BY 1 ORDER BY 1"""
    for grp, where, title in [("league", "", "by league"), ("CASE WHEN tte_min < 240 THEN 'a in-play/near' WHEN tte_min < 1440 THEN 'b 4-24h' ELSE 'c > 1 day' END", "", "by time to game end"),
                              ("CASE WHEN p_spread >= 0.5 THEN 'spread leg is favourite-ish (p>=0.5)' ELSE 'spread leg p<0.5' END", "", "by spread leg price")]:
        rows2 = con.execute(q.format(grp=grp, where=where)).fetchall(); hdr = [d[0] for d in con.execute(q.format(grp=grp, where=where)).description]
        R[f"same-game spread+total combos {title}"] = [hdr] + [list(r) for r in rows2]
        print(f"\nSame-game spread + total two-leg combos, {title}"); print(" | ".join(hdr))
        for r in rows2: print(" | ".join(str(x) for x in r))
    json.dump(R, open(os.path.join(HERE, "results_correlation.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
