"""Declared DIP evidence score, not a calibrated aesthetic or marketplace rating."""

# Fixed 100-point budget. Product weights apply once to the worst intended region.
WEIGHTS = {
    'photo.resolution': 8, 'photo.blur': 14, 'photo.contrast': 8,
    'photo.noise': 10, 'photo.shadow_loss': 5, 'photo.highlight_loss': 5,
    'product.detail': 4, 'product.blur': 8, 'product.noise': 4,
    'product.contrast': 2, 'product.clipping': 4,
    'product.mask_completeness': 3, 'product.edge': 5,
    'photo.group_size': 4, 'photo.group_placement': 3, 'photo.group_margins': 3,
    'photo.coverage': 10,
}
LABELS = {'resolution':'Resolution','blur':'Sharpness','contrast':'Contrast','noise':'Noise',
          'shadow_loss':'Shadow detail','highlight_loss':'Highlight detail','detail':'Product detail',
          'clipping':'Clipping warning','mask_completeness':'Mask completeness','edge':'Possible cropping',
          'group_size':'Group size','group_placement':'Group placement','group_margins':'Group margins',
          'coverage':'Intended-product coverage'}


def score_photo(checks, products, verdict):
    """Credit only supported Passes; missing/disabled/review checks retain their budget."""
    by_id = {c['identifier']: c for c in checks}
    intended = [p for p in products if p.get('intended', True)]
    components = []
    for key, weight in WEIGHTS.items():
        ids = ([f"product-{p.get('region_id',p['number'])}.{key.split('.')[1]}" for p in intended]
               if key.startswith('product.') else [key])
        findings = [by_id.get(identity) for identity in ids]
        states = [c['status'] if c and c.get('required', True) else 'Unassessed' for c in findings]
        state = ('Fail' if 'Fail' in states else 'Pass' if states and all(s == 'Pass' for s in states)
                 else 'Unassessed')
        unresolved = [identity for identity, s in zip(ids, states) if s not in {'Pass', 'Fail'}]
        components.append({
            'component': key, 'weight': weight, 'status': state,
            'earned': weight if state == 'Pass' else 0,
            'deducted': weight if state == 'Fail' else 0,
            'withheld': weight if state == 'Unassessed' else 0,
            'finding_ids': ids, 'unresolved_finding_ids': unresolved,
            'reason': ('No intended product region is available.' if not ids else
                       ' / '.join(c['explanation'] if c else 'Required finding is missing.' for c in findings)),
        })
    earned = sum(c['earned'] for c in components)
    lost = sum(c['deducted'] for c in components)
    withheld = sum(c['withheld'] for c in components)
    return {
        'version': 'dip-evidence-score-v1', 'score': earned, 'maximum': 100,
        'possible_range': [earned, earned + withheld],
        'assessed_coverage_percent': 100 - withheld,
        'confirmed_deductions': lost, 'uncertainty_withheld': withheld,
        'verdict': 'Manual Review' if verdict == 'Pass' and withheld else str(verdict),
        'method': 'Fixed weights; Pass earns full weight, Fail loses it, and Review/missing/disabled earns zero. '
                  'Each product component uses the worst intended region. Excluded regions remain context. '
                  'The range adds unresolved weight only; it is not a statistical confidence interval.',
        'limitation': 'Declared pilot weights, not a validated aesthetic rating. Coverage means decisive check weight, '
                      'not detected image area or verified masks. Unverified masks and uncalibrated local warning checks remain limitations.',
        'components': components,
        'main_deductions': sorted([c for c in components if c['deducted'] or c['withheld']],
                                  key=lambda c: (-(c['deducted'] + c['withheld']), c['component'])),
    }


def score_statement(score):
    main=list(dict.fromkeys(LABELS[c['component'].split('.')[1]].lower() for c in score['main_deductions']))[:2]
    reason=' and '.join(main)
    if score['confirmed_deductions']:
        return f"{score['confirmed_deductions']} points deducted for confirmed findings. Prioritize {reason}. Unassessed checks earn no credit."
    if score['uncertainty_withheld']:
        return f"No confirmed weighted failures. Review {reason}; {score['uncertainty_withheld']} points remain unassessed."
    return 'All weighted checks passed for the selected products. This is a provisional DIP assessment.'
