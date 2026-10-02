"""Fill-simulation engine for three DKeX strategies, replayed against the public time & sales (minute-stamped prints).
DKeX has no public order book, so a resting order is assumed to be filled when a print occurs at a price that would have
hit it: a print strictly through our price fills us in full (we were the best quote), a print exactly at our price fills
half of its quantity (queue share). This is optimistic on queue position and silent on whether the exchange would route a
customer's combo order to a third-party quote at all; it is the same convention as the Kalshi longshot recorder.

Strategies (each returns a list of fills and updates a state dict so the paper trader can carry orders across days):
  combo_followon   after a combo symbol's first print at p0, rest an offer at p0 - improve; fill on later prints.
  moneyline_maker  pre-game moneylines: quote fair +/- k around the Shin-devigged fair of the latest print pair; fill on prints through us.
  last_minutes     sell YES on moneylines printing at lo..hi cents in the final max_min minutes before settlement (replay-only).
  combo_desk       baseline: sell every combo at its print (what the desk does)."""
import math, datetime as dt
from devig import shin

TICK = 0.01


def _rows(con, sql, params):
    return con.execute(sql, params).fetchall()


def combo_followon(con, b0, b1, state, improve=0.01, size=100, max_per_symbol=1000, equal_share=0.5):
    """state: {'orders': {symbol: [price, remaining, placed_ts_iso]}}"""
    orders = state.setdefault("orders", {})
    fills = []
    rows = _rows(con, """SELECT t.symbol, t.ts, t.price, t.qty, c.settle FROM trades t JOIN contracts c USING(symbol)
        WHERE t.symbol LIKE 'COMBO-%' AND t.bdate BETWEEN ? AND ? ORDER BY t.ts, t.symbol, t.price""", [b0, b1])
    for sym, ts, price, qty, settle in rows:
        o = orders.get(sym)
        if o is None:
            p = round(price - improve, 2)
            if p >= TICK:
                orders[sym] = [p, min(size, max_per_symbol), ts.isoformat()]
            else:
                orders[sym] = [None, 0, ts.isoformat()]
            continue
        if not o[0] or o[1] <= 0:
            continue
        if price > o[0] + 1e-9:
            q = min(o[1], qty)
        elif abs(price - o[0]) < 1e-9:
            q = min(o[1], int(qty * equal_share))
        else:
            continue
        if q <= 0:
            continue
        o[1] -= q
        fills.append(dict(strategy="combo_followon", symbol=sym, ts=ts, side="sell", price=o[0], qty=q))
    # drop orders on settled symbols
    settled = {r[0] for r in _rows(con, "SELECT symbol FROM contracts WHERE symbol LIKE 'COMBO-%' AND settle IS NOT NULL AND symbol IN (SELECT symbol FROM trades WHERE bdate BETWEEN ? AND ?)", [b0, b1])}
    for s in settled:
        orders.pop(s, None)
    return fills


def moneyline_maker(con, b0, b1, state, k=0.01, size=100, max_inv=500, min_hours=3.5, pair_window_min=15, equal_share=0.5):
    """state: {'last': {symbol: [price, ts_iso]}, 'inv': {symbol: net_yes_contracts}}"""
    last = state.setdefault("last", {}); inv = state.setdefault("inv", {})
    fills = []
    rows = _rows(con, """SELECT t.symbol, t.ts, t.price, t.qty, split_part(t.symbol, '-', 4) AS event, c.league,
        date_diff('second', t.ts, c.settle_ts)/3600.0 AS h FROM trades t JOIN contracts c USING(symbol)
        WHERE c.mtype='WIN' AND c.period='FG' AND c.settle IS NOT NULL AND c.settle_ts IS NOT NULL AND t.bdate BETWEEN ? AND ? ORDER BY t.ts, t.symbol""", [b0, b1])
    # map event -> symbols
    pairs = {}
    for r in _rows(con, "SELECT split_part(symbol,'-',4), symbol FROM contracts WHERE mtype='WIN' AND period='FG' AND league IN ('NFL','CFB','MLB','WNBA','NHL','NBA')", []):
        pairs.setdefault(r[0], []).append(r[1])
    for sym, ts, price, qty, event, league, h in rows:
        others = [s for s in pairs.get(event, []) if s != sym]
        if h >= min_hours and len(others) == 1 and sym in last and others[0] in last:
            ps, ts_s = last[sym]; po, ts_o = last[others[0]]
            if not isinstance(ts_o, dt.datetime): ts_o = dt.datetime.fromisoformat(ts_o)
            if abs((ts - ts_o).total_seconds()) <= pair_window_min * 60 and 0 < ps < 1 and 0 < po < 1 and ps + po > 1.0:
                fair = shin([ps, po])[0]
                ask, bid = round(fair + k, 2), round(fair - k, 2)
                cur = inv.get(sym, 0)
                if price > ask + 1e-9 or abs(price - ask) < 1e-9:
                    q = min(qty if price > ask + 1e-9 else int(qty * equal_share), size, max(0, max_inv + cur))
                    if q > 0:
                        inv[sym] = cur - q; fills.append(dict(strategy="moneyline_maker", symbol=sym, ts=ts, side="sell", price=ask, qty=q, fair=fair))
                elif price < bid - 1e-9 or abs(price - bid) < 1e-9:
                    q = min(qty if price < bid - 1e-9 else int(qty * equal_share), size, max(0, max_inv - cur))
                    if q > 0:
                        inv[sym] = cur + q; fills.append(dict(strategy="moneyline_maker", symbol=sym, ts=ts, side="buy", price=bid, qty=q, fair=fair))
        last[sym] = [price, ts]
    # forget settled symbols
    settled = {r[0] for r in _rows(con, "SELECT symbol FROM contracts WHERE mtype='WIN' AND settle IS NOT NULL AND symbol IN (SELECT symbol FROM trades WHERE bdate BETWEEN ? AND ?)", [b0, b1])}
    for s in [s for s in list(last) if s in settled]:
        last.pop(s, None); inv.pop(s, None)
    for k, v in last.items():                      # keep state JSON-serialisable for the paper trader
        if isinstance(v[1], dt.datetime): v[1] = v[1].isoformat()
    return fills


def last_minutes(con, b0, b1, state, max_min=10, lo=0.05, hi=0.40, size=100, slip=0.01):
    fills = []
    rows = _rows(con, """SELECT t.symbol, t.ts, t.price, t.qty FROM trades t JOIN contracts c USING(symbol)
        WHERE c.mtype='WIN' AND c.period='FG' AND c.settle IS NOT NULL AND c.settle_ts IS NOT NULL AND t.bdate BETWEEN ? AND ?
        AND date_diff('second', t.ts, c.settle_ts) BETWEEN 0 AND ? AND t.price BETWEEN ? AND ?""", [b0, b1, max_min * 60, lo, hi])
    for sym, ts, price, qty in rows:
        fills.append(dict(strategy="last_minutes", symbol=sym, ts=ts, side="sell", price=round(price - slip, 2), qty=min(qty, size)))
    return fills


def combo_desk(con, b0, b1, state, size=100):
    rows = _rows(con, "SELECT symbol, ts, price, qty FROM trades WHERE symbol LIKE 'COMBO-%' AND bdate BETWEEN ? AND ?", [b0, b1])
    return [dict(strategy="combo_desk", symbol=s, ts=ts, side="sell", price=p, qty=min(q, size)) for s, ts, p, q in rows]


STRATEGIES = {"combo_followon": combo_followon, "moneyline_maker": moneyline_maker, "last_minutes": last_minutes, "combo_desk": combo_desk}
