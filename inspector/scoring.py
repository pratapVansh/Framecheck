"""Declared DIP evidence score, not a calibrated aesthetic or marketplace rating."""
import math

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
DEFAULT_VERDICT_POLICY = {'pass_threshold':75}
# These defects block approval even when other checks earn enough points.
CRITICAL_CHECKS = frozenset({'photo.resolution','photo.blur','photo.noise',
    'photo.shadow_loss','photo.highlight_loss','product.blur','product.noise',
    'product.edge','product.mask_completeness','photo.coverage'})


def score_policy(policy):
    """Keep malformed settings reviewable and safe to save as strict JSON."""
    valid=(isinstance(policy,dict) and set(policy)=={'pass_threshold'} and
           type(policy['pass_threshold']) in {int,float} and 0<=policy['pass_threshold']<=100 and
           math.isfinite(policy['pass_threshold']))
    return {'pass_threshold':policy['pass_threshold'] if valid else None}


def score_verdict(components, checks, earned, policy):
    """All required evidence must be complete; a confirmed critical defect wins over uncertainty."""
    policy=score_policy(policy)
    critical=[]
    incomplete=[]
    included_regions={identity.split('.',1)[0] for component in components
                      for identity in component['finding_ids'] if identity.startswith('product-')}
    for check in checks:
        if not check.get('required',True):continue
        identifier=check['identifier']
        if identifier.startswith('product-') and identifier.split('.',1)[0] not in included_regions:continue
        key='product.'+identifier.split('.',1)[1] if identifier.startswith('product-') else identifier
        severe_framing=(key in {'photo.group_size','photo.group_placement','photo.group_margins'} and
                        check.get('measurement',{}).get('severe_framing') is True)
        if (key in CRITICAL_CHECKS or severe_framing) and check['status']=='Fail':critical.append(identifier)
        if check['status'] not in {'Pass','Fail'} or (key not in WEIGHTS and check['status']!='Pass'):
            incomplete.append(identifier)
    for component in components:
        incomplete.extend(component['unresolved_finding_ids'])
        if not component['finding_ids']:incomplete.append(component['component'])
    if policy['pass_threshold'] is None:verdict,reason='Needs Review','invalid_score_policy'
    elif critical:verdict,reason='Needs Improvement','critical_defect'
    elif incomplete:verdict,reason='Needs Review','incomplete_checks'
    elif earned<policy['pass_threshold']:verdict,reason='Needs Improvement','below_threshold'
    else:verdict,reason='Pass','threshold_met'
    return {'verdict':verdict,'verdict_reason':reason,
        'verdict_policy':{'version':'score-threshold-v2','provisional':True,
            'pass_threshold':policy['pass_threshold'],
            'critical_checks':sorted(CRITICAL_CHECKS),'essential_checks':'all required checks and score components',
            'severe_framing_rule':'Two declared framing rules fail, or group extent is below half the declared minimum.'},
        'critical_failures':critical,'incomplete_checks':list(dict.fromkeys(incomplete))}


def score_photo(checks, products, verdict, *, verdict_policy=DEFAULT_VERDICT_POLICY):
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
            'blocked_by': sorted({c['measurement']['blocked_by'] for c in findings
                                  if c and c.get('measurement',{}).get('blocked_by')}),
            'reason': ('No intended product region is available.' if not ids else
                       ' / '.join(c['explanation'] if c else 'Required finding is missing.' for c in findings)),
        })
    earned = sum(c['earned'] for c in components)
    lost = sum(c['deducted'] for c in components)
    withheld = sum(c['withheld'] for c in components)
    result = {
        'version': 'dip-evidence-score-v1', 'score': earned, 'maximum': 100,
        'possible_range': [earned, earned + withheld],
        'assessed_coverage_percent': 100 - withheld,
        'confirmed_deductions': lost, 'uncertainty_withheld': withheld,
        'check_verdict':str(verdict),
        'method': 'Fixed weights; Pass earns full weight, Fail loses it, and Review/missing/disabled earns zero. '
                  'Each product component uses the worst intended region. Excluded regions remain context. '
                  'The range adds unresolved weight only; it is not a statistical confidence interval.',
        'limitation': 'Declared pilot weights, not a validated aesthetic rating. Coverage means decisive check weight, '
                      'not detected image area or verified masks. Unverified masks and uncalibrated local warning checks remain limitations.',
        'components': components,
        'main_deductions': sorted([c for c in components if c['deducted'] or c['withheld']],
                                  key=lambda c: (-(c['deducted'] + c['withheld']), c['component'])),
    }
    result.update(score_verdict(components,checks,earned,verdict_policy))
    return result


def score_statement(score):
    if score.get('verdict_reason')=='invalid_score_policy':
        return 'The score was measured, but the Pass threshold setting is invalid. Review the policy under Details before using this result.'
    labels={
        'photo.resolution':'photo size','photo.blur':'photo focus',
        'photo.contrast':'visible photo detail','photo.noise':'photo graininess',
        'photo.shadow_loss':'detail in dark areas','photo.highlight_loss':'detail in bright areas',
        'product.detail':'product detail','product.blur':'product focus',
        'product.noise':'product graininess','product.contrast':'visible product detail',
        'product.clipping':'bright and dark product detail','product.mask_completeness':'the product outline',
        'product.edge':'whether the product is cut off','photo.group_size':'how much of the photo the products fill',
        'photo.group_placement':'where the products sit in the photo','photo.group_margins':'space around the products',
        'photo.coverage':'whether all products are marked',
    }
    def names(components):
        return ' and '.join(dict.fromkeys(labels['photo.coverage' if c.get('blocked_by')==['photo.coverage']
                                               else c['component']] for c in components))
    passed=[c for c in score['components'] if c['status']=='Pass']
    problems=[c for c in score['main_deductions'] if c['deducted']]
    incomplete=[c for c in score['main_deductions'] if c['withheld'] or c.get('unresolved_finding_ids')]
    lost=score['confirmed_deductions'];pending=score['uncertainty_withheld']
    if not lost and not pending:
        return ('All checks included in this score passed. No points were lost. '+
            ('Keep the original resolution and leave clear space around every product.' if score['verdict']=='Pass' else
             'The overall result still needs attention. Review the findings below.'))
    parts=[names(passed[:2]).capitalize()+' passed.' if passed else 'No checks have passed yet.']
    parts.append(f'{lost} points were lost for problems with {names(problems[:2])}.' if lost else
                 'No points were lost for confirmed problems.')
    if pending:
        parts.append(f'{pending} points are pending because checks for {names([c for c in incomplete if c["withheld"]][:2])} are incomplete. '
                     'Incomplete checks do not confirm a problem.')
    elif incomplete:
        parts.append(f'Some checks for {names(incomplete[:2])} are still incomplete.')
    if score.get('verdict_reason')=='critical_defect':
        parts.append('A critical problem must be fixed before this photo can pass.')
    parts.append('This meets the provisional Pass threshold. Follow the suggestions below to improve it.' if score['verdict']=='Pass' else
                 'Follow the fixes below and review incomplete checks under Details.' if lost and incomplete else
                 'Follow the fixes below and upload a new photo.' if lost else
                 'Check the photo at full size and review these checks under Details.')
    return ' '.join(parts)
