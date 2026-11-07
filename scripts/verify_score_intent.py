"""Verify score/intent on the saved user photo and native procedural framing controls.

Run with: .venv/Scripts/python.exe -m scripts.verify_score_intent
Private source photos/checkpoints remain local; no production history or Groq calls.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from inspector.auto_inspect import amend_inspection, inspect_auto, detected_regions
from inspector.images import png_bytes
from inspector.modern_ui import render_result
from inspector.scoring import WEIGHTS
from inspector.storage import load_inspection, save_inspection


ROOT = Path(__file__).resolve().parents[1]


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'reports/multi-product-debug/d041db85-0eb8-4f7a-98a5-dcbb6135c94d/original.png')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/score-intent-verification')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protected = [ROOT / 'data/inspections.sqlite3', ROOT / 'data/one-click-policy.json']
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    cases = [('two-shirts', args.source.read_bytes(), 'Existing user photo, not independent validation.')]
    for name, top in [('full-length', 65), ('dominant-cropped', 0)]:
        rgb = np.full((1000, 800, 3), 245, np.uint8)
        yy, xx = np.indices((870, 320))
        texture = (40 + xx % 100 + 40 * ((yy // 2) % 2)).astype(np.uint8)
        rgb[top:top+870, 240:560] = texture[:, :, None]
        cases.append((name, png_bytes(rgb), 'Native procedural tall-product framing control; not a natural photo or category accuracy claim.'))
    rows = []
    for name, raw, provenance in cases:
        result = inspect_auto(raw)
        report = result.to_dict()
        products = detected_regions(result.segmentation)
        intended = [p for p in products if p['intended']]
        assert sum(WEIGHTS.values()) == 100
        score = report['quality_score']
        assert score['score'] + score['confirmed_deductions'] + score['uncertainty_withheld'] == 100
        assert score['score'] <= score['assessed_coverage_percent']
        assert score['uncertainty_withheld'] >= 10  # Automatic scope is still unconfirmed.
        if name == 'two-shirts':
            assert len(products) == 4 and len(intended) == 2
            assert all(p['label'] == 'Top' for p in intended)
            assert all(p['label'] == 'Trousers' for p in products if not p['intended'])
        else:
            assert len(intended) == 1
            edge = next(c for c in result.checks if c.identifier == f"product-{intended[0]['region_id']}.edge")
            assert edge.required
            assert (edge.reason_code == 'possible_cropping') == (name == 'dominant-cropped')
        database = args.output / 'checks.sqlite3'
        save_inspection(result, name + '.png', database)
        saved = load_inspection(result.inspection_id, database)
        assert json.loads(saved['result_json']) == report
        page = render_result(saved)
        assert 'Assessed coverage' in page and 'Whole-photo quality' in page
        (args.output / (name + '.json')).write_text(result.to_json(), encoding='utf-8')
        (args.output / (name + '.html')).write_text(page, encoding='utf-8')
        (args.output / (name + '.png')).write_bytes(result.original_png)
        if name == 'two-shirts':
            restored = amend_inspection(saved, intended_numbers=[p['region_id'] for p in products], confirm_coverage=True)
            assert all(p['intended'] for p in restored.segmentation['products'])
            assert saved['result_json'] == result.to_json()
        rows.append({'case': name, 'provenance': provenance, 'input_sha256': hashlib.sha256(raw).hexdigest(),
                     'intended': [p['number'] for p in intended], 'context': [p['region_id'] for p in products if not p['intended']],
                     'score': score['score'], 'verdict': score['verdict'],
                     'assessed_coverage_percent': score['assessed_coverage_percent'],
                     'uncertainty_withheld': score['uncertainty_withheld']})
        print(name, rows[-1], flush=True)
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    (args.output / 'verification.json').write_text(json.dumps({'cases': rows, 'protected_hashes': before}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    run()
