# Runtime configuration and local data

Three small configuration files are distributed with the source:

- `one-click-policy.json`: the provisional normal-resolution DIP thresholds.
- `quality-v3-profile.json`: the preserved guarded small-photo profile used by advanced tools.
- `quality-v3-profile.calibration.json`: its development evidence and integrity hashes.

Keep these files byte-for-byte. The calibration loader checks the profile and frozen core files; it does not silently accept modified thresholds or code. Historical path keys retain their original provenance. The loader uses their filenames, so a checkout can be relocated or used on another operating system without accessing those historical paths.

Everything else in this directory stays local and is ignored by Git: uploaded/saved image evidence, `inspections.sqlite3`, downloaded datasets, generated variants, private regression photos, and previous calibration assets. The application creates its database when an inspection is saved; no database needs to be downloaded.

The normal-resolution policy is a limited pilot, not verified marketplace accuracy. See [evaluation and limitations](../docs/evaluation.md).
