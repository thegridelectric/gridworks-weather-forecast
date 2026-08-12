# Samples

Canonical JSON instances, one per seeded **type** version that carries
an `examples:` block. Generated from the authored examples (never edited
by hand) and consumed by `roundtrip.py`. A type version without a sample
is silently untested by the round-trip, so its absence is recorded here.

Coverage: **6 of 8** seeded type versions have a sample.

Seeded type versions lacking a sample (no `examples:`):

- `glitch.000`
- `weather.000`
