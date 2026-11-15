#!/usr/bin/env python3
"""Group Epicollect5 oak-sapling records into trees.

The script does all the cheap, deterministic work: fetching entries, finding
nearby trees, re-using earlier photo comparisons, and writing the mapping.
The only thing it cannot do is look at photos. When a photo comparison is
needed it writes a task to work/pending.json (with shrunken photos alongside),
and stops. Claude (or a person) looks at the photos and records a verdict with
the `record` command, then runs `step` again. Repeat until nothing is pending.

    python3 scripts/group_trees.py step            # assign what it can, list pending comparisons
    python3 scripts/group_trees.py record A B P "reason"   # P = probability same tree, 0..1
    python3 scripts/group_trees.py record --file verdicts.json

Files (all in data/):
    config.json        settings (search radius, look-back, thresholds, ...)
    tree_mapping.csv   one row per record -> tree_id. Rows with status
                       "confirmed" are human-checked and never changed.
    comparisons.json   every photo comparison ever made, so none is repeated.
"""

import csv
import json
import math
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")
CONFIG_PATH = os.path.join(DATA, "config.json")
MAPPING_PATH = os.path.join(DATA, "tree_mapping.csv")
COMPARISONS_PATH = os.path.join(DATA, "comparisons.json")
PENDING_PATH = os.path.join(WORK, "pending.json")

FIELDS = ["record_id", "tree_id", "status", "confidence", "record_date",
          "compared_with", "reason", "updated"]


# ── Loading & saving ────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_mapping():
    if not os.path.exists(MAPPING_PATH):
        return {}
    with open(MAPPING_PATH, newline="") as f:
        return {r["record_id"].strip(): r for r in csv.DictReader(f) if r.get("record_id", "").strip()}


def save_mapping(mapping):
    rows = sorted(mapping.values(), key=lambda r: (tree_sort_key(r["tree_id"]), r.get("record_date", ""), r["record_id"]))
    with open(MAPPING_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def load_comparisons():
    if not os.path.exists(COMPARISONS_PATH):
        return {}
    with open(COMPARISONS_PATH) as f:
        items = json.load(f)
    return {pair_key(c["a"], c["b"]): c for c in items}


def save_comparisons(comps):
    items = sorted(comps.values(), key=lambda c: (c.get("date", ""), c["a"], c["b"]))
    with open(COMPARISONS_PATH, "w") as f:
        json.dump(items, f, indent=1)
        f.write("\n")


def pair_key(a, b):
    return "|".join(sorted([a, b]))


def tree_sort_key(tid):
    digits = "".join(ch for ch in tid if ch.isdigit())
    return (int(digits) if digits else 10**9, tid)


# ── Epicollect5 ─────────────────────────────────────────────────────────────

def fetch_entries(slug):
    url = f"https://five.epicollect.net/api/export/entries/{slug}?per_page=100"
    entries = []
    while url:
        with urllib.request.urlopen(url, timeout=60) as res:
            page = json.load(res)
        entries += page.get("data", {}).get("entries", [])
        url = (page.get("links") or {}).get("next")
    return entries


def parse_entry(e):
    """Return a record dict, or None if it has no usable location."""
    lat = lng = None
    photos = []
    for k, v in e.items():
        if isinstance(v, dict) and "latitude" in v and "longitude" in v:
            try:
                lat, lng = float(v["latitude"]), float(v["longitude"])
            except (TypeError, ValueError):
                pass
        elif isinstance(v, str) and "type=photo" in v and v.startswith("http"):
            photos.append((k, v))
    if lat is None:
        return None
    photos.sort()  # field names start with 1_, 2_, ... so this keeps the form order
    created = e.get("created_at", "")
    return {
        "id": e.get("title") or e.get("ec5_uuid"),
        "created": created,
        "date": created[:10],
        "lat": lat, "lng": lng,
        "photos": [u for _, u in photos],
    }


def distance_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, [a["lat"], a["lng"], b["lat"], b["lng"]])
    x = (lo2 - lo1) * math.cos((la1 + la2) / 2)
    return 6371000 * math.hypot(x, la2 - la1)


# ── Photos for pending comparisons ──────────────────────────────────────────

def fetch_photo(url, dest, max_px):
    if os.path.exists(dest):
        return dest
    tmp = dest + ".orig"
    urllib.request.urlretrieve(url, tmp)
    try:
        from PIL import Image  # optional
        with Image.open(tmp) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            im.save(dest, "JPEG", quality=80)
        os.remove(tmp)
    except ImportError:
        if shutil.which("sips"):  # macOS
            subprocess.run(["sips", "-Z", str(max_px), tmp, "--out", dest],
                           check=True, capture_output=True)
            os.remove(tmp)
        else:
            os.replace(tmp, dest)  # no resizer available: keep full size
    return dest


# ── Core logic ──────────────────────────────────────────────────────────────

def classify(p, cfg):
    if p >= cfg["same_threshold"]:
        return "same"
    if p <= cfg["different_threshold"]:
        return "different"
    return "uncertain"


def step(cfg, entries=None):
    today = date.today().isoformat()
    raw = entries if entries is not None else fetch_entries(cfg["project_slug"])
    records = {}
    for e in raw:
        r = parse_entry(e)
        if r and r["id"] and r["lat"] >= cfg["min_latitude"]:
            records[r["id"]] = r

    mapping = load_mapping()
    comps = load_comparisons()
    radius = cfg["search_radius_m"]

    # Current trees, built from the mapping (records missing from Epicollect are ignored).
    trees = {}
    for rid, row in mapping.items():
        if rid in records:
            trees.setdefault(row["tree_id"], []).append(rid)
    next_num = max([tree_sort_key(t)[0] for t in mapping_tree_ids(mapping)] + [0]) + 1

    new = sorted((r for r in records.values() if r["id"] not in mapping),
                 key=lambda r: (r["created"], r["id"]))
    blocked = []          # records waiting on a comparison (or near one that is)
    tasks = []
    added = {"auto": 0, "uncertain": 0}

    for r in new:
        if any(distance_m(r, records[b]) <= radius for b in blocked):
            blocked.append(r["id"])
            continue

        results = []      # (tree_id, verdict, probability, compared_with, note)
        pending_here = []
        skipped_same_day = []
        for tid, members in trees.items():
            if min(distance_m(r, records[m]) for m in members) > radius:
                continue
            if cfg["skip_same_day"] and any(records[m]["date"] == r["date"] for m in members):
                skipped_same_day.append(tid)
                continue
            chain = sorted(members, key=lambda m: records[m]["created"], reverse=True)[:cfg["max_lookback"]]
            best_p, best_with, outcome = 0.0, chain[0], None
            for m in chain:
                c = comps.get(pair_key(r["id"], m))
                if c is None:
                    pending_here.append((r, records[m], tid))
                    outcome = "pending"
                    break
                p = float(c["same_probability"])
                v = classify(p, cfg)
                if v == "uncertain":
                    if p > best_p or best_with is None:
                        best_p, best_with = p, m
                    continue          # look further back in this tree's history
                best_p, best_with, outcome = p, m, v
                break
            if outcome == "pending":
                continue
            results.append((tid, outcome or "uncertain", best_p, best_with))

        if pending_here:
            blocked.append(r["id"])
            tasks += pending_here
            continue

        row = {"record_id": r["id"], "record_date": r["date"], "updated": today}
        same = sorted([x for x in results if x[1] == "same"], key=lambda x: -x[2])
        unsure = sorted([x for x in results if x[1] == "uncertain"], key=lambda x: -x[2])
        note = f" Same-day rule excluded {', '.join(skipped_same_day)}." if skipped_same_day else ""

        if same:
            tid, _, p, w = same[0]
            row.update(tree_id=tid, confidence=f"{p:.2f}", compared_with=w)
            if len(same) > 1 or unsure:
                rivals = ", ".join(x[0] for x in (same[1:] + unsure))
                row.update(status="uncertain", reason=f"Matched {tid} but also possibly {rivals}.{note}")
            else:
                row.update(status="auto", reason=f"Photos match {tid}.{note}")
        elif unsure and unsure[0][2] >= 0.5:
            tid, _, p, w = unsure[0]
            row.update(tree_id=tid, status="uncertain", confidence=f"{p:.2f}", compared_with=w,
                       reason=f"Unclear after {cfg['max_lookback']} look-backs; best guess {tid}.{note}")
        else:
            tid = f"T{next_num:03d}"
            next_num += 1
            if unsure:
                row.update(status="uncertain", confidence=f"{1 - unsure[0][2]:.2f}", compared_with=unsure[0][3],
                           reason=f"New tree; could be {', '.join(x[0] for x in unsure)}.{note}")
            else:
                checked = [x[0] for x in results]
                row.update(status="auto", confidence="1.00" if not checked else
                           f"{1 - max(x[2] for x in results):.2f}",
                           compared_with=",".join(x[3] for x in results),
                           reason=("New tree; differs from " + ", ".join(checked) if checked
                                   else "New tree; no other tree nearby") + "." + note)
            row["tree_id"] = tid
        mapping[r["id"]] = row
        trees.setdefault(row["tree_id"], []).append(r["id"])
        added[row["status"]] += 1

    save_mapping(mapping)
    write_pending(tasks, cfg)
    deferred = len(blocked) - len({t[0]["id"] for t in tasks})
    print(f"Records: {len(records)} mapped area, {len(new)} new this run.")
    print(f"Assigned now: {added['auto']} auto, {added['uncertain']} uncertain.")
    print(f"Pending photo comparisons: {len(tasks)} (another {deferred} records wait on them).")
    if tasks:
        print(f"See {os.path.relpath(PENDING_PATH, ROOT)}; record verdicts, then run `step` again.")
    else:
        print("Nothing pending. Done.")
    return len(tasks)


def mapping_tree_ids(mapping):
    return {row["tree_id"] for row in mapping.values() if row.get("tree_id")}


def write_pending(tasks, cfg):
    os.makedirs(os.path.join(WORK, "img"), exist_ok=True)
    out = []
    for new_rec, old_rec, tid in tasks:
        def imgs(rec):
            return [fetch_photo(u, os.path.join(WORK, "img", f"{rec['id']}_p{i + 1}.jpg"), cfg["photo_max_px"])
                    for i, u in enumerate(rec["photos"])]
        out.append({
            "new_record": new_rec["id"], "new_date": new_rec["date"], "new_photos": imgs(new_rec),
            "tree_id": tid,
            "tree_record": old_rec["id"], "tree_record_date": old_rec["date"], "tree_photos": imgs(old_rec),
            "distance_m": round(distance_m(new_rec, old_rec), 1),
        })
    with open(PENDING_PATH, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")


def record(args):
    comps = load_comparisons()
    today = date.today().isoformat()
    if args[:1] == ["--file"]:
        with open(args[1]) as f:
            verdicts = json.load(f)
    else:
        a, b, p = args[0], args[1], args[2]
        verdicts = [{"a": a, "b": b, "same_probability": p, "reason": " ".join(args[3:])}]
    for v in verdicts:
        p = float(v["same_probability"])
        if not 0 <= p <= 1:
            sys.exit(f"same_probability must be 0..1, got {p}")
        a, b = sorted([v["a"], v["b"]])
        comps[pair_key(a, b)] = {"a": a, "b": b, "same_probability": round(p, 2),
                                 "reason": v.get("reason", ""), "source": v.get("source", "claude"),
                                 "date": today}
    save_comparisons(comps)
    print(f"Recorded {len(verdicts)} verdict(s); {len(comps)} comparisons stored.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("step", "record"):
        sys.exit(__doc__)
    cfg = load_config()
    if sys.argv[1] == "step":
        entries = None
        if "--entries" in sys.argv:  # offline testing with a saved export
            with open(sys.argv[sys.argv.index("--entries") + 1]) as f:
                entries = json.load(f)
        step(cfg, entries)
    else:
        record(sys.argv[2:])


if __name__ == "__main__":
    main()
