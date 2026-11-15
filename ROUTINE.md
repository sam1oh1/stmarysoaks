# Tree grouping routine

Instructions for the scheduled Claude run that groups Epicollect5 records
into trees. People don't need to read this; see "Reviewing the mapping" in
README.md instead.

## Steps

0. `pip install --quiet pillow` (shrinks photos before you view them; skip if it fails).
1. `python3 scripts/group_trees.py step`
   - If it says "Nothing pending", go to step 4.
2. Open `work/pending.json`. Each task asks whether `new_record` is the same
   tree as `tree_record`, which already belongs to tree `tree_id`. Look at the
   photo files listed in `new_photos` and `tree_photos`, and judge each task
   using the guide below. Write all the verdicts into one file,
   `work/verdicts.json`:
   ```json
   [{"a": "<new_record>", "b": "<tree_record>", "same_probability": 0.85,
     "reason": "Same gap in hawthorn hedge, path on left; leader forks at same height"}]
   ```
   Then run `python3 scripts/group_trees.py record --file work/verdicts.json`.
3. Go back to step 1. Older records are offered automatically when a verdict
   is uncertain. Stop after 15 rounds, even if work remains; the next run
   carries on from there.
4. If `data/` changed, commit `data/tree_mapping.csv` and
   `data/comparisons.json` with the message
   "Tree grouping: N new records (X uncertain)" and push to `main`.
   Never edit rows whose status is `confirmed`.

## How to judge a pair

Each record has two photos. Photo 1 is a wide view of the setting, and photo 2
is a close-up of the sapling. The records may be months apart, in different
seasons, and the sapling will have grown. Many saplings are planted along the
same hedgerows, so neighbours look alike.

Weigh the evidence in this order:

1. **Setting (most reliable).** Look for the same fixed features in photo 1 and
   photo 2 backgrounds: the shape and gaps of the hedge, paths and their
   curve, fences, posts, gates, large trees, buildings and field edges on the
   horizon. Seasonal change (leaves vs bare) does not count against a match.
2. **Sapling structure.** Look at the main stem's lean and kinks, where it
   forks, the number and angle of main branches, and any tree guard or stake.
   Allow for growth and for leaf loss between visits.
3. **Height.** Use it only relative to nearby things (grass, guard, hedge),
   because nothing gives a fixed scale. Expect it to grow, never to shrink a lot.

Give `same_probability`:
- **0.85–1.0** when the setting clearly matches and nothing in the sapling
  contradicts it.
- **0.0–0.15** when the setting clearly differs, or the sapling is
  incompatible, for example it is much smaller in the later visit.
- **Between 0.25 and 0.75** when you cannot tell. That is fine: the job will
  then compare with an older visit of the same tree and mark the result for a
  person to review.

Keep `reason` short and concrete; a reviewer reads it.
