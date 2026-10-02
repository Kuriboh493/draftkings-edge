"""DraftKings sportsbook hold (overround) by market family from the category snapshots: two-way markets, paired Over/Under
props at the same line, and multi-outcome futures where the listed selections exhaust the field. Results in results_dk_hold.json."""
import os, json, glob, collections, statistics as S
from devig import power, shin

HERE = os.path.dirname(os.path.abspath(__file__))
DK = os.path.join(HERE, "data", "dk")


def analyse(dd):
    subs = {x["id"]: x["name"].strip() for x in dd["subcategories"]}
    sel = collections.defaultdict(list)
    for x in dd["selections"]:
        if x.get("trueOdds"): sel[x["marketId"]].append(x)
    res = collections.defaultdict(list); fut = []
    for m in dd["markets"]:
        ss = sel[m["id"]]
        if len(ss) < 2: continue
        sub = subs.get(m.get("subcategoryId"), "?")
        byline = collections.defaultdict(dict)
        for x in ss:
            lab = (x.get("label") or "").split()
            if x.get("points") is not None and lab and lab[0] in ("Over", "Under"): byline[x["points"]][lab[0]] = x
        pairs = [(p, v) for p, v in byline.items() if "Over" in v and "Under" in v]
        if pairs:
            main = [(p, v) for p, v in pairs if v["Over"].get("main") or "MainPointLine" in v["Over"].get("tags", [])] or pairs
            for p, v in pairs:
                res[(sub, "main O/U" if (p, v) in main else "alt O/U")].append(1 / v["Over"]["trueOdds"] + 1 / v["Under"]["trueOdds"] - 1)
        elif len(ss) == 2 and all(x.get("points") is None for x in ss) and {x.get("label") for x in ss} not in ({"Yes", "No"},) and not any(k in m["name"] for k in ("Scorer", "Home Runs", "Points", "Goal")):
            res[(sub, "2-way")].append(1 / ss[0]["trueOdds"] + 1 / ss[1]["trueOdds"] - 1)
        elif len(ss) >= 6 and all(x.get("points") is None for x in ss) and any(k in m["name"] for k in ("Winner", "Champion", "MVP", "Seed", "Wins", "Finals", "Heisman", "Cup")):
            inv = sum(1 / x["trueOdds"] for x in ss)
            raw = [1 / x["trueOdds"] for x in ss]; qs, z = shin(raw, True); qp = power(raw)
            i = raw.index(min(raw)); j = raw.index(max(raw))
            fut.append(dict(name=m["name"], selections=len(ss), hold=inv - 1, favourite=max(raw), longest=min(raw), shin_z=z,
                            longest_fair_shin=qs[i], longest_fair_power=qp[i], favourite_fair_shin=qs[j], favourite_fair_power=qp[j],
                            longest_markup_shin=min(raw) / qs[i], favourite_markup_shin=max(raw) / qs[j]))
    return res, fut


def main():
    out = {}
    for f in sorted(glob.glob(os.path.join(DK, "*_cat_*.json"))) + sorted(glob.glob(os.path.join(DK, "league_*.json"))):
        dd = json.load(open(f)); lg = dd["leagues"][0]["name"] if dd.get("leagues") else "?"
        res, fut = analyse(dd)
        key = os.path.basename(f).replace(".json", "")
        out[key] = {"league": lg, "holds": {f"{a}|{b}": {"n": len(v), "mean": S.mean(v), "min": min(v), "max": max(v)} for (a, b), v in res.items()}, "futures": fut}
        if res or fut: print(f"\n=== {lg} ({key})")
        for (a, b), v in sorted(res.items(), key=lambda x: -len(x[1]))[:10]: print(f"  {a[:30]:30s} {b:9s} n={len(v):4d} mean hold {S.mean(v)*100:5.2f}% (min {min(v)*100:.1f} max {max(v)*100:.1f})")
        for x in fut[:8]: print(f"  FUTURE {x['name'][:50]:50s} sel={x['selections']:3d} hold={x['hold']*100:6.1f}% fav={x['favourite']*100:.1f}% (Shin fair {x['favourite_fair_shin']*100:.1f}%, x{x['favourite_markup_shin']:.2f}) longest={x['longest']*100:.2f}% (Shin fair {x['longest_fair_shin']*100:.3f}%, x{x['longest_markup_shin']:.1f}) z={x['shin_z']*100:.1f}%")
    json.dump(out, open(os.path.join(HERE, "results_dk_hold.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
