# stmarysoaks
Oak sapling monitoring site: https://sam1oh1.github.io/stmarysoaks/

The page loads every record live from the Epicollect5 project
`oak-sapling-recorder` and shows **one dot per tree**. Records of the same
tree, made on different visits, are grouped using `data/tree_mapping.csv`.
Clicking a dot shows that tree's photos, newest first; scroll sideways for
older visits.

## How records are grouped

A scheduled Claude routine (instructions in [ROUTINE.md](ROUTINE.md)) runs
`scripts/group_trees.py`, which:

1. Fetches all records and picks out those not yet in the mapping.
2. Looks for existing trees within `search_radius_m` of each new record (GPS
   is only accurate to a few metres). Trees with a record on the **same day**
   are skipped, as one tree isn't recorded twice in a day.
3. If no tree is nearby, the record starts a new tree, with no photo check.
4. Otherwise Claude compares the photos with the tree's most recent visit.
   If that's inconclusive it tries the next older visit, up to
   `max_lookback` visits.
5. Saves every photo comparison in `data/comparisons.json`, so no pair is
   ever compared twice.

Records south of latitude 51.81 are left out, as on the map.

Until the routine has processed a new record, the site shows it as its own
dot ("not yet grouped").

## Settings (`data/config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `search_radius_m` | 10 | How far apart two records can be and still be compared |
| `max_lookback` | 3 | How many older visits of a tree to try when a comparison is unclear |
| `skip_same_day` | true | Treat records made on the same day as different trees |
| `same_threshold` / `different_threshold` | 0.75 / 0.25 | Confidence needed to call a pair the same / different; anything between is "uncertain" |
| `min_latitude` | 51.81 | Records south of this are ignored |
| `photo_max_px` | 700 | Photos are shrunk to this size before comparison, to save usage |

## Reviewing the mapping

Open `data/tree_mapping.csv` in Excel, or edit it on GitHub. Each row is one
record:

| Column | Meaning |
|---|---|
| `record_id` | Epicollect5 record title |
| `tree_id` | The tree it belongs to (`T001`, `T002`, ...) |
| `status` | `auto` (decided with confidence), `uncertain` (best guess, **please review**), or `confirmed` (checked by a person) |
| `confidence` | 0–1 confidence in the decision |
| `record_date` | Date the record was made |
| `compared_with` | The record(s) its photos were compared with |
| `reason` | Short explanation |

To review:
1. Filter `status` to `uncertain`.
2. Compare the record's photos with those of the `compared_with` record (search
   either ID in Epicollect5, or click the tree on the site).
3. Fix `tree_id` if needed. To make a record its own tree, give it an unused
   number. Then set `status` to `confirmed`.

The routine never changes `confirmed` rows, and it builds new groupings on
top of them.
