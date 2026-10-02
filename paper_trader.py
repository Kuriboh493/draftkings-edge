"""Daily paper trader for the DKeX strategies: fetch the newest Railbird reports, rebuild the database, replay each new
business date's prints against the virtual resting orders carried over from previous days, and record fills in
data/paper/paper.db. Nothing is sent anywhere. Run once a day (the launchd plist does it at 11:30); report.py summarises.
State per strategy is a JSON blob (open orders, last prints, inventory) so orders posted on one day fill on later days."""
import os, sys, json, sqlite3, datetime as dt, duckdb
import fetch_dkex, build_dkex
from strategies import STRATEGIES

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "data", "paper"); DB = os.path.join(PAPER, "paper.db"); LOG = os.path.join(PAPER, "paper.log")
START_BDATE = int(os.environ.get("PAPER_START", "20261001"))
ACTIVE = {"combo_followon": {}, "moneyline_maker": {}, "last_minutes": {}}   # last_minutes is replay-only (needs settlement time)
MAKER_FEE, TAKER_FEE = 0.0025, {"lo": 0.01, "mid": 0.02}                      # DKeX: maker $0.0025/contract; taker 1c below 20c and above 96c, else 2c


def log(msg):
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"; print(line, flush=True)
    open(LOG, "a").write(line + "\n")


def taker_fee(price):
    return TAKER_FEE["lo"] if price < 0.20 or price > 0.96 else TAKER_FEE["mid"]


def main():
    os.makedirs(PAPER, exist_ok=True)
    if "--no-fetch" not in sys.argv:
        log("fetching reports"); fetch_dkex.main()
        log("rebuilding database"); build_dkex.main()
    st = sqlite3.connect(DB)
    st.executescript("""CREATE TABLE IF NOT EXISTS state(strategy TEXT PRIMARY KEY, blob TEXT);
    CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY, strategy TEXT, symbol TEXT, ts TEXT, bdate INT, side TEXT, price REAL, qty INT, fee REAL, fair REAL);
    CREATE TABLE IF NOT EXISTS processed(bdate INT PRIMARY KEY, run_ts TEXT, fills INT);""")
    con = duckdb.connect(os.path.join(HERE, "data", "dkex", "dkex.duckdb"), read_only=True)
    done = {r[0] for r in st.execute("SELECT bdate FROM processed")}
    dates = [r[0] for r in con.execute("SELECT DISTINCT bdate FROM trades WHERE bdate >= ? ORDER BY 1", [START_BDATE]).fetchall() if r[0] not in done]
    # skip the most recent date if its settlement report is not in yet (contracts settled that day would be missing); reports arrive together, so process all
    for b in dates:
        n = 0
        for name, kw in ACTIVE.items():
            row = st.execute("SELECT blob FROM state WHERE strategy=?", (name,)).fetchone()
            state = json.loads(row[0]) if row else {}
            fills = STRATEGIES[name](con, b, b, state, **kw)
            for f in fills:
                fee = MAKER_FEE if name != "last_minutes" else taker_fee(f["price"])
                st.execute("INSERT INTO fills(strategy, symbol, ts, bdate, side, price, qty, fee, fair) VALUES (?,?,?,?,?,?,?,?,?)",
                           (name, f["symbol"], f["ts"].isoformat(), b, f["side"], f["price"], f["qty"], fee, f.get("fair")))
            st.execute("INSERT OR REPLACE INTO state(strategy, blob) VALUES (?,?)", (name, json.dumps(state)))
            n += len(fills)
        st.execute("INSERT INTO processed(bdate, run_ts, fills) VALUES (?,?,?)", (b, dt.datetime.now().isoformat(timespec='seconds'), n))
        st.commit(); log(f"processed {b}: {n} fills")
    log("done")


if __name__ == "__main__":
    main()
