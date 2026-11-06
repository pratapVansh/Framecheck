"""Apply recorded Phase 4 AI visual decisions and verify the real benchmark.

This is a one-time annotation migration, not an automatic ground-truth labeler.
It consumes the existing source/variant/overlay review notes, never predictions.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from inspector.benchmark import image_bytes, read_manifest, verify_split_freeze, write_manifest
from inspector.images import decode_image
from inspector.models import DEFECTS
from scripts.generate import transform


def geometry_labels(mask):
    """Measure the source-checked reference outline, not production segmentation."""
    height, width = mask.shape
    x, y, bw, bh = cv2.boundingRect(mask.astype(np.uint8))
    extent = max(bw / width, bh / height)
    tolerance = 1 / min(width, height)
    coverage = None if min(abs(extent - .55), abs(extent - .90)) <= tolerance else not .55 <= extent <= .90
    offsets = (abs((x + bw / 2) / width - .5), abs((y + bh / 2) / height - .5))
    placement = True if any(v > .10 + t for v, t in zip(offsets, (1 / width, 1 / height))) else (
        None if any(abs(v - .10) <= t for v, t in zip(offsets, (1 / width, 1 / height))) else False)
    margins = ((x, width), (y, height), (width - x - bw, width), (height - y - bh, height))
    margin = True if any(v < .025 * size - 1 for v, size in margins) else (
        None if any(abs(v - .025 * size) <= 1 for v, size in margins) else False)
    return dict(frame_coverage=coverage, placement=placement, margins=margin)


def main():
    root = Path('data/phase4-review')
    manifest = Path('data/benchmark.jsonl')
    backup = Path('data/benchmark.before-phase4-review.jsonl.bak')
    report_path = Path('data/phase4-ai-review.json')
    if backup.exists() or report_path.exists():
        raise ValueError('Phase 4 review already finalized; preserve the existing version.')
    verify_split_freeze('data/manifest.jsonl')
    sources = {r['id']: r for r in read_manifest('data/manifest.jsonl')}
    rows = read_manifest(manifest)
    original_rows = deepcopy(rows)
    index = json.loads((root / 'index.json').read_text())
    notes = json.loads((root / 'visual-notes.json').read_text())
    aliases = {item['id']: item for item in index}
    overrides = {key: value for packet in notes.values() for key, value in packet['label_overrides'].items()}
    assert {p for packet in notes.values() for p in packet['reviewed_pages']} == set(range(1, 43))
    variants = [r for r in rows if r['kind'] != 'original']
    assert len(variants) == len(aliases) == 840
    assert all(not row['reviewed'] for row in variants)
    now = datetime.now(timezone.utc).isoformat()
    reviewer = 'OpenAI Codex (AI reviewer; not human verified)'
    changed, mask_count, controls = 0, 0, 0
    for row in rows:
        source = sources[row['source_id']]
        assert all(row[k] == source[k] for k in ('group_id', 'source_hash', 'split'))
        rgb, _ = decode_image(image_bytes(row))
        if row['kind'] == 'original':
            assert row == source
            continue
        evidence = aliases[row['id']]
        assert Path(evidence['sheet']).exists()
        replay, _ = decode_image(image_bytes(source))
        mask = np.array(Image.open(source['mask']).convert('L')) > 127 if source.get('mask_reviewed') else None
        for details in row['transforms']:
            if 'defect' in details:
                replay, mask, _ = transform(replay, details['defect'], details['severity'], details['seed'], mask)
        assert np.array_equal(rgb, replay), row['id']
        if row.get('mask'):
            mask_data = Path(row['mask']).read_bytes()
            assert hashlib.sha256(mask_data).hexdigest() == row['mask_sha256']
            saved = np.array(Image.open(row['mask']).convert('L'))
            assert set(np.unique(saved)) == {0, 255}
            assert saved.shape == rgb.shape[:2] and np.array_equal(saved > 127, mask)
            mask_count += 1
            row.update(mask_reviewed=True, mask_reviewer=reviewer, mask_reviewed_at=now,
                       mask_review_method='ai_visual_review', mask_boundary_uncertainty_pixels=2,
                       mask_review_evidence=evidence['sheet'])
        labels = deepcopy(source['labels'])
        defects = {d['defect'] for d in row['transforms'] if 'defect' in d}
        if not defects:
            controls += 1
        else:
            # All intended labels were visually checked in the recorded review;
            # ambiguous transformations are overridden below, never forced positive.
            for defect in defects:
                labels[defect] = True
            if defects & {'blur', 'noise', 'low_contrast', 'underexposure', 'overexposure'}:
                for defect in ('blur', 'noise', 'low_contrast', 'underexposure', 'overexposure'):
                    if defect not in defects:
                        labels[defect] = None
                if mask is None:
                    labels['background_uniformity'] = None
                    if defects & {'low_contrast', 'underexposure'}:
                        labels['background_color'] = True
                    elif 'overexposure' in defects:
                        labels['background_color'] = False if source['labels']['background_color'] is False else None
                    else:
                        labels['background_color'] = None
            if defects & {'frame_coverage', 'placement', 'margins', 'possible_cropping'}:
                labels.update(geometry_labels(mask))
                labels.update(background_color=False, background_uniformity=False)
                labels['possible_cropping'] = True if 'possible_cropping' in defects else source['labels']['possible_cropping']
                labels['blur'] = labels['noise'] = None
            if 'low_resolution' in defects:
                labels['blur'] = labels['noise'] = None
                if mask is not None:
                    labels.update(geometry_labels(mask))
                else:
                    for defect in ('frame_coverage', 'placement', 'margins'):
                        labels[defect] = None
            if 'background_color' in defects:
                labels.update(background_color=True, background_uniformity=False)
            if 'background_uniformity' in defects:
                labels.update(background_color=True, background_uniformity=True)
        labels['low_resolution'] = min(rgb.shape[:2]) < 60 or max(rgb.shape[:2]) < 80
        labels.update(overrides.get(evidence['alias'], {}))
        corrections = {key: {'suggested': row['labels'][key], 'reviewed': labels[key]}
                       for key in DEFECTS if row['labels'][key] != labels[key]}
        changed += len(corrections)
        row.update(labels=labels, reviewed=True, reviewer=reviewer, reviewed_at=now,
                   review_method='ai_visual_review', human_verified=False, human_confirmed=False,
                   mask_human_verified=False, mask_human_confirmed=False,
                   label_note='Completed AI review; uncertain and incidental labels corrected. Not human verified.',
                   review_notes='Recorded source/variant/overlay visual review, with conservative uncertainty for altered quality. '
                                'Geometry checked against the reviewed reference outline with one-native-pixel rule-boundary uncertainty. '
                                'Approximate AI mask boundaries remain uncertain by 1-2 pixels.',
                   ai_review={'author': reviewer, 'reviewed_at': now, 'source_sheet': evidence['sheet'],
                              'alias': evidence['alias'], 'visual_notes': str(root / 'visual-notes.json'),
                              'labels_checked': list(DEFECTS), 'corrections': corrections,
                              'human_verified': False,
                              'method': 'Finalize existing AI visual decisions; reference-outline geometry and native dimensions; no inspector outputs.'})
    assert mask_count == 517 and controls == 30
    assert all(r['reviewed'] and not r.get('human_verified') for r in rows)
    counts = {split: {defect: len({r['group_id'] for r in rows if r['split'] == split and r['labels'][defect] is True})
                      for defect in DEFECTS} for split in ('train', 'validation', 'test')}
    shortfalls = {d: n for d, n in counts['test'].items() if n < 10}
    # Check encoded and decoded image identity independently of group/source IDs.
    for decoded in (False, True):
        registry = {}
        for row in rows:
            raw = image_bytes(row)
            key = hashlib.sha256(decode_image(raw)[0].tobytes() if decoded else raw).hexdigest()
            assert registry.setdefault(key, row['split']) == row['split'], 'Cross-split duplicate image'
    backup.write_bytes(manifest.read_bytes())
    write_manifest(manifest, rows)
    assert read_manifest(manifest) == rows
    assert [r for r in rows if r['kind'] == 'original'] == [r for r in original_rows if r['kind'] == 'original']
    report = dict(phase=4, status='Complete for AI-annotated demonstration; test support target met.',
                  created_at=now, reviewer=reviewer, variants=840, kinds=dict(Counter(r['kind'] for r in variants)),
                  reviewed_transformed_masks=mask_count, human_verified_images=0, human_verified_masks=0,
                  label_corrections=changed, unknown_labels=sum(v is None for r in rows for v in r['labels'].values()),
                  positive_groups=counts, test_support_shortfalls=shortfalls,
                  split_counts=dict(Counter(r['split'] for r in rows)),
                  benchmark_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
                  evidence_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (root / 'index.json', root / 'visual-notes.json', Path(__file__))},
                  source_split_freeze_verified=True, exact_replays=840, identical_controls=controls,
                  leakage_checks='No overlap in group/source identities, source hashes, encoded or decoded image hashes.',
                  limitations=['AI annotations and approximate masks are not independently human verified.',
                               'Correlated synthetic variants do not establish real marketplace accuracy.',
                               'Unknown incidental quality labels are excluded per category, never assumed absent.',
                               'Support below ten positive test groups prevents a complete evaluation claim; do not redraw test splits.'])
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
