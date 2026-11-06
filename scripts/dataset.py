import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

import cv2
import numpy as np

from inspector.benchmark import assert_group_splits, image_bytes, read_manifest, rules_hash, write_manifest
from inspector.images import decode_image
from inspector.models import DEFECTS, InspectionProfile, PhotoContext


def fingerprint(rgb):
    gray = cv2.cvtColor(cv2.resize(rgb, (32, 32)), cv2.COLOR_RGB2GRAY).astype(np.float32)
    dct = cv2.dct(gray)[:8, :8].ravel()[1:]
    bits = dct > np.median(dct)
    return sum(int(value) << index for index, value in enumerate(bits))


def category_of(row):
    if row.get("masterCategory", "").lower() == "apparel":
        return "apparel"
    if row.get("masterCategory", "").lower() == "footwear":
        return "footwear"
    sub, article = row.get("subCategory", "").lower(), row.get("articleType", "").lower()
    if sub == "bags" or article in {"handbags", "backpacks", "clutches", "duffel bag", "laptop bag"}:
        return "bags"
    if sub == "watches" or article == "watches":
        return "watches"
    return None


def audit(root: Path, output: Path, limit: int, seed: int):
    if output.exists():
        raise ValueError("Output manifest already exists. Choose a new location to preserve reviewed labels.")
    csv_files = list(root.rglob("styles.csv"))
    if len(csv_files) != 1:
        raise ValueError("Dataset directory must contain exactly one styles.csv from Fashion Product Images Small.")
    images = {path.stem: path for path in root.rglob("*.jpg")}
    if not images:
        raise ValueError("No dataset JPG images were found.")
    buckets, issues, dimensions = defaultdict(list), [], Counter()
    with csv_files[0].open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if not {"id", "masterCategory", "subCategory", "articleType"} <= set(header):
            raise ValueError("styles.csv lacks expected Fashion Product Images Small metadata columns.")
        for line, cells in enumerate(reader, 2):
            if len(cells) != len(header):
                issues.append({"line": line, "issue": "Malformed metadata column count"})
                continue
            row = dict(zip(header, cells))
            category = category_of(row)
            if category:
                buckets[category].append(row)
    rng = random.Random(seed)
    selected, duplicate_hashes, perceptual = [], {}, []
    quota = max(1, limit // 4)
    for category in ("apparel", "footwear", "bags", "watches"):
        candidates = buckets[category]
        rng.shuffle(candidates)
        accepted = 0
        for item in candidates:
            if accepted >= quota:
                break
            source_id = str(item["id"])
            image = images.get(source_id)
            if image is None:
                issues.append({"id": source_id, "issue": "Missing image"})
                continue
            try:
                data = image.read_bytes()
                rgb, _ = decode_image(data)
            except ValueError as exc:
                issues.append({"id": source_id, "issue": str(exc)})
                continue
            decoded_hash = hashlib.sha256(rgb.tobytes() + str(rgb.shape).encode()).hexdigest()
            group = duplicate_hashes.setdefault(decoded_hash, source_id)
            if group != source_id:
                issues.append({"id": source_id, "issue": "Exact duplicate grouped with " + group})
            h, w = rgb.shape[:2]
            dimensions[f"{w}x{h}"] += 1
            selected.append({
                "id": source_id, "source_id": source_id, "group_id": group,
                "source_hash": decoded_hash, "image": str(image.resolve()), "category": category,
                "image_sha256": hashlib.sha256(data).hexdigest(),
                "width": w, "height": h, "kind": "original", "severity": "original",
                "labels": dict.fromkeys(DEFECTS), "reviewed": False, "split": None,
                "photo_context": PhotoContext.UNCERTAIN,
                "rules_hash": rules_hash(InspectionProfile()), "mask": None,
                "source_dataset": "paramaggarwal/fashion-product-images-small", "metadata": item,
            })
            perceptual.append((source_id, fingerprint(rgb)))
            accepted += group == source_id
    near_duplicates = [{"first": first, "second": second, "hamming": (a ^ b).bit_count()}
                       for index, (first, a) in enumerate(perceptual) for second, b in perceptual[index + 1:] if (a ^ b).bit_count() <= 4]
    write_manifest(output, selected)
    report = {"source_dataset": "paramaggarwal/fashion-product-images-small", "seed": seed,
              "selected_images": len(selected), "selected_groups": len(set(row["group_id"] for row in selected)),
              "category_counts": dict(Counter(row["category"] for row in selected)),
              "selected_dimensions": dict(dimensions), "issues": issues, "duplicate_candidates": near_duplicates,
              "note": "Review duplicate candidates and assign matching group IDs before splitting. Dimension distribution covers selected images, not the full archive."}
    output.with_suffix(".audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Selected {len(selected)} images. Review labels, masks, and duplicate candidates before splitting.")


def split_manifest(manifest: Path, seed: int, min_mask_groups: int = 20):
    if type(min_mask_groups) is not int or min_mask_groups < 0:
        raise ValueError("Minimum mask groups must be a nonnegative integer.")
    before = manifest.read_bytes()
    rows = read_manifest(manifest)
    if any(row["split"] is not None for row in rows):
        raise ValueError("Splits are already assigned. Do not resplit an existing benchmark.")
    if not rows or not all(row["reviewed"] for row in rows):
        raise ValueError("Review every selected original before splitting.")
    if any(row.get("kind") != "original" for row in rows):
        raise ValueError("Assign splits before generating variants.")
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    identities, masked_groups = ({}, {}, {}), set()
    for row in rows:
        data = image_bytes(row)
        rgb, _ = decode_image(data)
        decoded_hash = hashlib.sha256(rgb.tobytes() + str(rgb.shape).encode()).hexdigest()
        if row.get("source_hash") and row["source_hash"] != decoded_hash:
            raise ValueError(f"Decoded source hash changed: {row['id']}.")
        for registry, key in zip(identities, (row["source_id"], decoded_hash, hashlib.sha256(data).hexdigest())):
            if registry.setdefault(key, row["group_id"]) != row["group_id"]:
                raise ValueError("Related sources and exact duplicates must share a group ID before splitting.")
        if row.get("mask_reviewed"):
            if not row.get("mask"):
                raise ValueError("A reviewed mask must have a saved image.")
            mask_data = Path(row["mask"]).read_bytes()
            if row.get("mask_sha256") and hashlib.sha256(mask_data).hexdigest() != row["mask_sha256"]:
                raise ValueError(f"Reviewed mask changed: {row['id']}.")
            mask, _ = decode_image(mask_data)
            if mask.shape != rgb.shape or not np.isin(mask, [0, 255]).all() or not np.all(mask == mask[:, :, :1]) or not mask.any() or mask.all():
                raise ValueError("Reviewed masks must be native-sized binary images with product and background.")
            masked_groups.add(row["group_id"])
    if len(masked_groups) < 3 * min_mask_groups:
        raise ValueError(f"Need at least {3 * min_mask_groups} distinct reviewed mask groups; found {len(masked_groups)}.")
    categories = defaultdict(list)
    for group, members in groups.items():
        if len({row["category"] for row in members}) != 1:
            raise ValueError("A product group cannot span categories for category-balanced splitting.")
        categories[members[0]["category"]].append(group)
    rng = random.Random(seed)
    splits = ("train", "validation", "test")
    mask_counts = Counter()
    for category in sorted(categories):
        category_groups = sorted(categories[category])
        n = len(category_groups)
        train_end, val_end = round(n * 0.6), round(n * 0.8)
        capacity = dict(zip(splits, (train_end, val_end - train_end, n - val_end)))
        allocated = {split: [] for split in splits}
        if min_mask_groups:
            masked = [group for group in category_groups if group in masked_groups]
            rng.shuffle(masked)
            for group in masked:
                available = [split for split in splits if len(allocated[split]) < capacity[split]]
                split = min(available, key=lambda name: (len(allocated[name]), mask_counts[name], splits.index(name)))
                allocated[split].append(group)
                mask_counts[split] += 1
            category_groups = [group for group in category_groups if group not in masked_groups]
        rng.shuffle(category_groups)
        remaining = iter(category_groups)
        for split in splits:
            allocated[split].extend(next(remaining) for _ in range(capacity[split] - len(allocated[split])))
            for group in allocated[split]:
                for row in groups[group]:
                    row["split"] = split
    assert_group_splits(rows)
    mask_counts = {split: len({row["group_id"] for row in rows if row["split"] == split and row["group_id"] in masked_groups}) for split in splits}
    if any(count < min_mask_groups for count in mask_counts.values()):
        raise ValueError("Category capacities cannot satisfy this mask allocation; review quotas before freezing splits.")
    backup = manifest.with_suffix(".before-split.jsonl.bak")
    frozen = manifest.with_suffix(".split-freeze.jsonl.bak")
    report_path = manifest.with_suffix(".split.json")
    if any(path.exists() for path in (backup, frozen, report_path)):
        raise ValueError("Split artifacts already exist. Preserve the frozen benchmark instead of overwriting it.")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "splitter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "group_ratios": {"train": 0.6, "validation": 0.2, "test": 0.2},
        "method": "Category-balanced 60/20/20 product groups; reviewed mask groups balanced first, then remaining groups; sorted identities and seeded shuffles; no detector scores or labels select assignments.",
        "min_mask_groups_per_split": min_mask_groups, "manifest": str(manifest), "frozen_manifest": str(frozen),
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "split_counts": {split: {"images": sum(row["split"] == split for row in rows),
                                  "groups": len({row["group_id"] for row in rows if row["split"] == split}),
                                  "reviewed_mask_groups": mask_counts[split],
                                  "categories": dict(Counter(row["category"] for row in rows if row["split"] == split)),
                                  "mask_categories": dict(Counter(groups[group][0]["category"] for group in masked_groups if groups[group][0]["split"] == split))} for split in splits},
        "annotation_methods": dict(Counter(row.get("review_method", "unrecorded") for row in rows)),
        "human_verified_images": sum(row.get("human_verified") is True for row in rows),
        "assignments": [{key: row.get(key) for key in ("id", "source_id", "group_id", "source_hash", "image_sha256", "mask_sha256", "category", "split", "review_method", "human_verified")} for row in rows],
        "leakage_check": {"group_id": "passed", "source_id": "passed", "decoded_source_hash": "passed", "image_sha256": "passed"},
        "source_and_reviewed_mask_hashes_verified": True,
        "limitations": ["Mask groups are deliberately balanced across splits rather than sampled at 60/20/20.", "Grouping depends on recorded review decisions; hashes cannot prove semantic product identity.", "AI review does not establish independent human verification."],
    }
    if manifest.read_bytes() != before:
        raise ValueError("Manifest changed during split preparation; retry from the current annotations.")
    created, changed = [], False
    try:
        with backup.open("xb") as handle:
            created.append(backup)
            handle.write(before)
        write_manifest(manifest, rows)
        changed = True
        frozen_bytes = manifest.read_bytes()
        report["frozen_sha256"] = hashlib.sha256(frozen_bytes).hexdigest()
        for path, content in ((frozen, frozen_bytes), (report_path, json.dumps(report, indent=2).encode("utf-8"))):
            with path.open("xb") as handle:
                created.append(path)
                handle.write(content)
    except Exception:
        if changed:
            rollback = manifest.with_suffix(".split-rollback.tmp")
            rollback.write_bytes(before)
            rollback.replace(manifest)
        for path in created:
            path.unlink(missing_ok=True)
        raise
    print(dict(Counter(row["split"] for row in rows)))
    return report


def main():
    parser = argparse.ArgumentParser(description="Audit only Fashion Product Images Small and create leakage-safe splits.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("audit")
    create.add_argument("--dataset", type=Path, required=True)
    create.add_argument("--output", type=Path, default=Path("data/manifest.jsonl"))
    create.add_argument("--limit", type=int, default=300)
    create.add_argument("--seed", type=int, default=42)
    split = sub.add_parser("split")
    split.add_argument("--manifest", type=Path, default=Path("data/manifest.jsonl"))
    split.add_argument("--seed", type=int, default=42)
    split.add_argument("--min-mask-groups", type=int, default=20)
    args = parser.parse_args()
    try:
        if args.command == "audit":
            if args.limit < 4:
                raise ValueError("Select at least four source products.")
            audit(args.dataset, args.output, args.limit, args.seed)
        else:
            split_manifest(args.manifest, args.seed, args.min_mask_groups)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
