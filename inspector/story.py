"""Read-only, evidence-approved wording shared by result cards and narration."""
from inspector.scoring import CRITICAL_CHECKS, WEIGHTS


_DEPENDENT_REASONS = {'intended_group_unconfirmed', 'intended_group_missing',
                      'intended_coverage_unconfirmed'}
_NAMES = {
    'resolution': 'photo size', 'blur': 'focus', 'noise': 'graininess',
    'contrast': 'visible detail', 'shadow_loss': 'detail in dark areas',
    'highlight_loss': 'detail in bright areas', 'detail': 'visible product detail',
    'clipping': 'detail in bright and dark areas', 'mask_completeness': 'the estimated outline',
    'edge': 'visibility near the photo edge', 'coverage': 'the intended product selection',
    'group_size': 'how much space the products fill', 'group_placement': 'product placement',
    'group_margins': 'space around the products',
}
_FAIL = {
    'resolution': 'The photo is too small for the size check.',
    'blur': 'Focus does not meet the check for clear detail.',
    'noise': 'Graininess is above the checked limit.',
    'contrast': 'Light and dark detail is too similar for this check.',
    'shadow_loss': 'Too much detail is lost in dark areas.',
    'highlight_loss': 'Too much detail is lost in bright areas.',
    'detail': 'There is too little product detail for this check.',
    'clipping': 'Too much product detail is lost in bright or dark areas.',
    'mask_completeness': 'The estimated outline does not meet the completeness check; this does not prove the product is incomplete.',
    'edge': 'The product reaches the photo edge and may be cut off.',
    'coverage': 'The intended product selection needs updating.',
    'group_size': 'The products do not meet the checked size within the photo.',
    'group_placement': 'The products sit too far from the center for this check.',
    'group_margins': 'There is too little space around the products for this check.',
}
_UNCERTAIN = {
    'resolution': 'We could not establish whether the original photo is large enough to review.',
    'blur': 'We could not judge focus reliably.',
    'noise': 'We could not tell whether graininess hides important detail.',
    'contrast': 'We could not tell whether light and dark details are clear enough.',
    'shadow_loss': 'Detail in dark areas is unclear.',
    'highlight_loss': 'Detail in bright areas is unclear.',
    'detail': 'We could not tell whether enough product detail is visible.',
    'clipping': 'Detail in bright and dark product areas is unclear.',
    'mask_completeness': 'The estimated outline may miss part of the product.',
    'edge': 'Part of the product may be outside the frame; we could not confirm this.',
    'coverage': 'We could not confirm which visible items are intended products.',
    'group_size': 'We could not judge how much of the photo the products should fill.',
    'group_placement': 'We could not judge the product group position reliably.',
    'group_margins': 'We could not judge the space around the products reliably.',
}


def _strength(key, scope):
    subject = 'The photo' if scope == 'Photo' else scope
    return {
        'blur': (["The photo’s overall focus looks good.", 'Overall, the photo looks in focus.'] if scope == 'Photo'
                 else [f'{subject} looks in focus.', f'Fine detail on {subject} looks sharp enough to review.']),
        'resolution': ['The original photo is large enough to review.', 'The original image is large enough for this review.'],
        'detail': [f'{subject} is large enough in the image to inspect.', f'{subject} occupies enough image space to review its detail.'],
        'noise': [f'{subject} has little enough graininess to review its detail.', f'Graininess in {subject.lower() if scope == "Photo" else subject} is within the accepted range.'],
        'contrast': [f'Light and dark detail in {subject.lower() if scope == "Photo" else subject} looks distinct.', f'{subject} has enough contrast to review visible detail.'],
    }.get(key)


def friendly_finding(check):
    """Describe one finding without converting uncertain evidence into a defect."""
    key = check.get('identifier', '').rsplit('.', 1)[-1]
    name = _NAMES.get(key, 'this part of the photo')
    status = check.get('status')
    if status not in {'Pass', 'Fail'}:
        observation = _UNCERTAIN.get(key, f'We could not judge {name} reliably.')
        if status in {'Disabled', 'Unassessed'}:
            observation = f'This inspection did not assess {name}.'
        return observation + ' This does not confirm a problem.'
    if status == 'Pass':
        if key == 'mask_completeness':
            return 'The estimated outline passed its check; its accuracy is not verified.'
        if key == 'edge':
            return 'The edge check passed; it cannot guarantee the whole product is visible.'
        strength = _strength(key, 'Photo' if check.get('identifier', '').startswith('photo.') else 'The product')
        return strength[0] if strength else f'The observed {name} meets the review requirements.'
    return _FAIL.get(key, 'This part of the photo did not meet its check.')


def _scope(identifier, numbers):
    prefix = identifier.split('.', 1)[0]
    return numbers.get(prefix, 'Photo')


def _action_text(check, incomplete):
    key = check['identifier'].rsplit('.', 1)[-1]
    if key == 'coverage':
        return ('Check the numbered items and select the products you want assessed.',
                'Confirm which numbered items are intended products; mark any that are missing.')
    suggestions = {
        'resolution': ('Choose a larger original photo; inspecting this same small file again will not add detail.', 'Use a higher-resolution original instead of enlarging or rechecking the same small file.'),
        'blur': ('Check focus at full size. If detail looks soft, use a sharper photo.', 'Open the original and check the fine detail; replace it if it looks out of focus.'),
        'noise': ('Check graininess at full size. If it hides detail, use a clearer photo.', 'Open the original; use a less grainy photo if the grain obscures detail.'),
        'contrast': ('Check whether light and dark details are easy to see; adjust the photo if they blend together.', 'Review the original and improve contrast if important details blend together.'),
        'shadow_loss': ('Check dark areas; use a photo with more visible shadow detail if needed.', 'Review the darkest areas and replace the photo if important detail is lost.'),
        'highlight_loss': ('Check bright areas; use a photo with more visible detail there if needed.', 'Review the brightest areas and replace the photo if important detail is lost.'),
        'detail': ('Check fine product detail at full size; use a closer, clearer photo if needed.', 'Open the original and use a more detailed photo if product features are hard to see.'),
        'clipping': ('Check bright and dark product areas; replace the photo if important detail is missing.', 'Review the lightest and darkest product details and use a clearer photo if needed.'),
        'mask_completeness': ('Compare the marked outlines with the original; correct any region that misses part of a product.', 'Review the estimated outlines and mark any incomplete product region again.'),
        'edge': ('Check products near the photo edge; use a wider photo if any part is cut off.', 'Open the original and check the edges; replace it only if part of an intended product is missing.'),
        'group_size': ('Check how much of the photo the products fill before changing the framing.', 'Review the product group in the original before adjusting its size in the photo.'),
        'group_placement': ('Check the product group position; move it nearer the center if needed.', 'Review the group placement and center the products together if needed.'),
        'group_margins': ('Check space around the products; leave a clear gap from the photo edges if needed.', 'Review the outer product edges and use more space around them if needed.'),
    }
    if key == 'resolution' and incomplete:
        return ('The original photo size was not assessed. Inspect the original file before deciding whether a larger photo is needed.',
                'We could not establish the photo size. Run a fresh inspection of the original file to check it.')
    if key == 'group_size' and not incomplete:
        value = check.get('measurement', {}).get('score')
        threshold = check.get('threshold', {})
        minimum, maximum = threshold.get('minimum'), threshold.get('maximum')
        numeric = lambda value: type(value) in {int, float}
        if numeric(value) and numeric(minimum) and value < minimum:
            return ('Move closer so the products fill more of the photo, keeping their edges visible.',
                    'Use tighter framing around the products while leaving their edges visible.')
        if numeric(value) and numeric(maximum) and value > maximum:
            return ('Step back so the products fit with more space around them.',
                    'Use wider framing to leave more space around the products.')
    texts = suggestions.get(key, ('Review this incomplete check in Details before relying on the result.',
                                 'Open Details to see what still needs checking.'))
    uncertain = (_UNCERTAIN.get(key, 'This part of the assessment is incomplete.') + ' ') if incomplete else ''
    return tuple(uncertain + text for text in texts)


def story_options(report):
    """Offer only evidence-bound phrases; never alter score, checks, or verdict."""
    segmentation = report.get('segmentation', {})
    products = segmentation.get('products', [])
    intended = sorted((p for p in products if p.get('intended', True)),
                      key=lambda p: p.get('region_id', p.get('number', 0)))
    numbers = {f"product-{p.get('region_id', p.get('number'))}": f'Product {n}'
               for n, p in enumerate(intended, 1)}
    checks = []
    for check in report.get('checks', []):
        identity = check.get('identifier', '')
        if not identity or not check.get('required', True):
            continue
        if identity.startswith('product-') and identity.split('.', 1)[0] not in numbers:
            continue
        checks.append(check)
    score = report.get('quality_score') or {}
    by_id = {c['identifier']: c for c in checks}
    if score:
        expected = [key for key in WEIGHTS if key.startswith('photo.')]
        expected.extend(f"{prefix}.{key.split('.', 1)[1]}" for prefix in numbers
                        for key in WEIGHTS if key.startswith('product.'))
        for component in score.get('components', []):
            expected.extend(component.get('finding_ids', []))
        for identity in dict.fromkeys(expected):
            if identity.startswith('product-') and identity.split('.', 1)[0] not in numbers:
                continue
            if identity not in by_id:
                check = {'identifier': identity, 'status': 'Unassessed'}
                checks.append(check)
                by_id[identity] = check
    failures = [c for c in checks if c.get('status') == 'Fail']
    incomplete = [c for c in checks if c.get('status') not in {'Pass', 'Fail'}]
    policy_invalid = score.get('verdict_reason') == 'invalid_score_policy'
    missing_component = any(not c.get('finding_ids') for c in score.get('components', []))
    verdict = score.get('verdict')
    all_clear = (verdict == 'Pass' and bool(checks) and bool(intended) and
                 not failures and not incomplete and not policy_invalid and not missing_component)
    if all_clear:
        headline = ['Your photo looks good.', 'Your photo looks good.']
        summary = ['Based on the completed checks, this photo is ready.',
                   'The photo and selected products passed this review.']
    elif verdict == 'Pass' and failures and not incomplete and not policy_invalid and not missing_component:
        headline = ['Your photo is ready, with room to improve.', 'Ready, with improvements worth making.']
        summary = ['The photo meets the overall requirements, but some checks found improvements to make.',
                   'The overall result is ready; the suggestions below address the remaining findings.']
    elif verdict == 'Needs Improvement' and failures and not policy_invalid:
        headline = ['Your photo needs an update.', 'Update this photo before using it.']
        summary = ['Checks found problems to address. Any incomplete checks are listed separately.',
                   'Start with the confirmed findings below; unfinished checks do not confirm additional defects.']
    else:
        headline = ['A closer look is needed.', 'Some checks still need a closer look.']
        summary = ['This is not a judgment that the photo is bad. Review what remains uncertain below.',
                   'We cannot confirm readiness yet. An incomplete check does not mean something is wrong.']
    actions = []

    def add(group, kind, coverage=False):
        check = {'identifier': 'photo.coverage'} if coverage else group[0]
        identity = check['identifier']
        key = identity.rsplit('.', 1)[-1]
        scopes = {_scope(c['identifier'], numbers) for c in group}
        ordered = [scope for scope in ['Photo', *numbers.values()] if scope in scopes]
        scope = (' and '.join(ordered) if len(ordered) < 3 else
                 ', '.join(ordered[:-1]) + ' and ' + ordered[-1])
        unassessed = any(c.get('status') in {'Disabled', 'Unassessed', None} for c in group)
        target = ('reinspect' if unassessed else 'selection' if key == 'coverage' else
                  'evidence' if key in {'mask_completeness', 'resolution'} else 'original')
        texts = list(_action_text(check, kind == 'incomplete'))
        if unassessed:
            texts = [text + ' Run a fresh inspection to try the missing checks again.' for text in texts]
        title = ('Use a larger original photo' if key == 'resolution' and kind == 'confirmed' else
                 ('Check ' if kind == 'incomplete' else 'Improve ') + _NAMES.get(key, 'this finding'))
        actions.append({'id': identity, 'kind': kind, 'finding_ids': [c['identifier'] for c in group],
                        'scope': scope, 'title': title, 'target': target, 'texts': texts})

    critical = set(score.get('critical_failures', []))
    def priority(check):
        identity = check['identifier']
        key = 'product.' + identity.split('.', 1)[1] if identity.startswith('product-') else identity
        return (0 if identity in critical or key in CRITICAL_CHECKS or
                check.get('measurement', {}).get('severe_framing') is True else 1, identity)
    def add_groups(findings, kind):
        groups = {}
        for check in findings:
            groups.setdefault(check['identifier'].rsplit('.', 1)[-1], []).append(check)
        for group in groups.values():
            add(group, kind)

    add_groups(sorted(failures, key=priority), 'confirmed')
    coverage_unresolved = any(c['identifier'] == 'photo.coverage' for c in incomplete)
    dependent = [c for c in incomplete if c['identifier'] == 'photo.coverage' or
                 c.get('measurement', {}).get('blocked_by') == 'photo.coverage' or
                 c.get('reason_code') in _DEPENDENT_REASONS or
                 (coverage_unresolved and c['identifier'] == 'photo.group_size' and
                  c.get('reason_code') == 'small_in_frame')]
    if dependent:
        add(sorted(dependent, key=lambda c: (c['identifier'] != 'photo.coverage', c['identifier'])),
            'incomplete', coverage=True)
    add_groups([c for c in incomplete if c not in dependent], 'incomplete')
    if incomplete:
        observations = ([_UNCERTAIN['coverage']] if dependent else [])
        keys = set()
        for check in incomplete:
            key = check['identifier'].rsplit('.', 1)[-1]
            if check in dependent or key in keys:
                continue
            keys.add(key)
            observations.append(_UNCERTAIN.get(key, 'Part of the photo assessment is unfinished.'))
        detail = ' '.join(observations[:3])
        if verdict == 'Needs Improvement' and failures and not policy_invalid:
            summary = ['Some changes are needed. Separately: ' + detail,
                       'Start with the improvements below. Still unclear: ' + detail]
        else:
            summary = [detail + ' This does not confirm a problem with the photo.',
                       "We cannot say it is ready yet. " + detail]
    if policy_invalid or not score or not intended or missing_component:
        actions.append({'id': 'assessment.incomplete', 'kind': 'incomplete', 'finding_ids': [],
                        'scope': 'Photo', 'title': 'Review the incomplete assessment', 'target': 'evidence',
                        'texts': ['The assessment is incomplete. Review Details before relying on this result.',
                                  'Read Details to see why readiness could not be established.']})
    good = []
    # Outline/edge passes are not evidence of a verified mask or a complete product.
    positive_keys = {'blur', 'resolution', 'noise', 'contrast', 'detail'}
    used_keys = set()
    for check in checks:
        key = check['identifier'].rsplit('.', 1)[-1]
        if check.get('status') != 'Pass' or key not in positive_keys or key in used_keys:
            continue
        scope = _scope(check['identifier'], numbers)
        good.append({'id': check['identifier'], 'finding_ids': [check['identifier']],
                     'texts': _strength(key, scope)})
        used_keys.add(key)
        if len(good) == 2:
            break
    return {'headline': headline, 'summary': summary, 'good': good, 'actions': actions[:3]}


def deterministic_story(options):
    """Choose the first approved phrase for a provider-independent fallback."""
    return {'headline': options['headline'][0], 'summary': options['summary'][0],
            **{key: [{'id': item['id'], 'finding_ids': list(item['finding_ids']), 'text': item['texts'][0]}
                     for item in options[key]] for key in ('good', 'actions')}}


def validate_story(payload, options):
    """Reject invented, missing, moved, duplicated, or reprioritized claims."""
    if not isinstance(payload, dict) or set(payload) != {'headline', 'summary', 'good', 'actions'}:
        raise ValueError('invalid_story_schema')
    for key in ('headline', 'summary'):
        if not isinstance(payload[key], str) or payload[key] not in options[key]:
            raise ValueError('unsupported_story_claim')
    for key in ('good', 'actions'):
        items = payload[key]
        approved = options[key]
        if not isinstance(items, list) or len(items) != len(approved):
            raise ValueError('missing_story_claims')
        allowed = {item['id']: item for item in approved}
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or set(item) != {'id', 'finding_ids', 'text'}:
                raise ValueError('invalid_story_schema')
            identity = item['id']
            if not isinstance(identity, str) or identity not in allowed or identity in seen:
                raise ValueError('unknown_or_duplicate_story_claim')
            expected = allowed[identity]
            if (item['finding_ids'] != expected['finding_ids'] or not isinstance(item['finding_ids'], list)
                    or not isinstance(item['text'], str) or item['text'] not in expected['texts']):
                raise ValueError('unsupported_story_claim')
            if key == 'actions' and identity != approved[index]['id']:
                raise ValueError('story_priority_changed')
            seen.add(identity)
    return payload
