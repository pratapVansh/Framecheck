"""Evidence wording and closed-claim validation; no model or provider required."""
import copy
import unittest

from inspector.scoring import WEIGHTS
from inspector.story import deterministic_story, friendly_finding, story_options, validate_story


def report(regions=(1,)):
    products = [{'number': region, 'region_id': region, 'intended': True} for region in regions]
    identities = [key for key in WEIGHTS if key.startswith('photo.')]
    identities += [f"product-{region}.{key.split('.')[1]}" for region in regions
                   for key in WEIGHTS if key.startswith('product.')]
    return {'segmentation': {'products': products},
            'quality_score': {'verdict': 'Pass', 'verdict_reason': 'threshold_met'},
            'checks': [{'identifier': identity, 'status': 'Pass'} for identity in identities]}


def change(result, identity, status, **kwargs):
    check = next(c for c in result['checks'] if c['identifier'] == identity)
    check.update(status=status, **kwargs)
    return check


class StoryTests(unittest.TestCase):
    def test_all_clear_is_read_only_and_has_no_preservation_advice(self):
        result = report()
        original = copy.deepcopy(result)
        options = story_options(result)
        story = deterministic_story(options)
        self.assertEqual(story['headline'], 'Your photo looks good.')
        self.assertEqual(story['actions'], [])
        self.assertLessEqual(len(story['good']), 2)
        self.assertEqual(story['good'][0]['text'], 'The original photo is large enough to review.')
        self.assertEqual(story['good'][1]['text'], 'The photo’s overall focus looks good.')
        self.assertEqual(validate_story(story, options), story)
        self.assertEqual(result, original)

    def test_pass_with_noncritical_failure(self):
        result = report()
        change(result, 'photo.contrast', 'Fail')
        options = story_options(result)
        self.assertIn('ready', options['headline'][0])
        self.assertNotIn('Your photo looks good.', options['headline'])
        self.assertEqual(options['actions'][0]['kind'], 'confirmed')

    def test_uncertain_focus_does_not_call_photo_bad_or_demand_retake(self):
        result = report()
        result['quality_score']['verdict'] = 'Needs Review'
        change(result, 'photo.blur', 'Manual Review')
        options = story_options(result)
        self.assertIn('closer look', options['headline'][0])
        action = options['actions'][0]
        self.assertEqual(action['finding_ids'], ['photo.blur'])
        self.assertEqual(action['kind'], 'incomplete')
        self.assertIn('If', action['texts'][0])
        self.assertIn('focus', options['summary'][0])

    def test_critical_then_noncritical_then_selection(self):
        result = report()
        result['quality_score']['verdict'] = 'Needs Improvement'
        change(result, 'photo.contrast', 'Fail')
        change(result, 'photo.resolution', 'Fail')
        change(result, 'photo.coverage', 'Manual Review')
        change(result, 'photo.blur', 'Manual Review')
        actions = story_options(result)['actions']
        self.assertEqual([a['id'] for a in actions], ['photo.resolution', 'photo.contrast', 'photo.coverage'])
        self.assertEqual([a['kind'] for a in actions], ['confirmed', 'confirmed', 'incomplete'])

    def test_dependent_grouping_preserves_independent_focus(self):
        result = report()
        result['quality_score']['verdict'] = 'Needs Review'
        change(result, 'photo.coverage', 'Manual Review')
        change(result, 'photo.group_size', 'Manual Review', measurement={'blocked_by': 'photo.coverage'})
        change(result, 'photo.group_placement', 'Manual Review', reason_code='intended_group_unconfirmed')
        change(result, 'photo.group_margins', 'Manual Review', reason_code='intended_group_missing')
        change(result, 'photo.blur', 'Manual Review')
        actions = story_options(result)['actions']
        self.assertEqual([a['id'] for a in actions], ['photo.coverage', 'photo.blur'])
        self.assertEqual(len(actions[0]['finding_ids']), 4)
        self.assertEqual(actions[0]['target'], 'selection')

    def test_stable_regions_map_to_current_product_numbers_and_exclusions(self):
        result = report((8, 3, 5))
        result['segmentation']['products'][1]['intended'] = False
        change(result, 'product-3.edge', 'Fail')
        change(result, 'product-5.blur', 'Fail')
        change(result, 'product-8.noise', 'Fail')
        result['checks'].append({'identifier': 'photo.foreground_coverage', 'status': 'Fail', 'required': False})
        options = story_options(result)
        actions = {a['id']: a for a in options['actions']}
        self.assertEqual(actions['product-5.blur']['scope'], 'Product 1')
        self.assertEqual(actions['product-8.noise']['scope'], 'Product 2')
        self.assertNotIn('Product 1:', actions['product-5.blur']['texts'][0])
        self.assertNotIn('product-3.edge', actions)
        self.assertNotIn('photo.foreground_coverage', actions)

    def test_missing_and_disabled_findings_are_incomplete(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                result = report()
                if missing:
                    result['checks'] = [c for c in result['checks'] if c['identifier'] != 'product-1.edge']
                else:
                    change(result, 'product-1.edge', 'Disabled')
                options = story_options(result)
                self.assertNotEqual(options['headline'][0], 'Your photo looks good.')
                self.assertEqual(options['actions'][0]['kind'], 'incomplete')
                self.assertEqual(options['actions'][0]['finding_ids'], ['product-1.edge'])
                self.assertEqual(options['actions'][0]['target'], 'reinspect')

    def test_invalid_policy_and_missing_score_are_plain_incomplete(self):
        for no_score in (True, False):
            result = report()
            if no_score:
                del result['quality_score']
            else:
                result['quality_score'].update(verdict='Needs Review', verdict_reason='invalid_score_policy')
            options = story_options(result)
            self.assertIn('closer look', options['headline'][0])
            self.assertEqual(options['actions'][0]['kind'], 'incomplete')
            self.assertNotIn('policy', options['actions'][0]['texts'][0])

    def test_empty_report_is_conservative(self):
        options = story_options({})
        self.assertIn('closer look', options['headline'][0])
        self.assertEqual(options['good'], [])
        self.assertTrue(options['actions'])

    def test_no_intended_products_and_empty_score_component_need_action(self):
        for result in (report(()), report()):
            result['quality_score']['components'] = [{'component': 'product.blur', 'finding_ids': []}]
            options = story_options(result)
            self.assertNotEqual(options['headline'][0], 'Your photo looks good.')
            self.assertEqual(options['actions'][-1]['id'], 'assessment.incomplete')

    def test_group_size_direction(self):
        for value, phrase in ((0.1, 'Move closer'), (0.95, 'Step back')):
            result = report()
            change(result, 'photo.group_size', 'Fail', measurement={'score': value},
                   threshold={'minimum': 0.3, 'maximum': 0.8})
            self.assertTrue(story_options(result)['actions'][0]['texts'][0].startswith(phrase))
        change(result, 'photo.group_size', 'Manual Review')
        self.assertNotIn('Step back', story_options(result)['actions'][0]['texts'][0])

    def test_multi_focus_is_one_action_with_all_regions(self):
        result = report((8, 3))
        for identity in ('photo.blur', 'product-3.blur', 'product-8.blur'):
            change(result, identity, 'Manual Review')
        options = story_options(result)
        self.assertEqual(len(options['actions']), 1)
        action = options['actions'][0]
        self.assertEqual(set(action['finding_ids']), {'photo.blur', 'product-3.blur', 'product-8.blur'})
        self.assertEqual(action['scope'], 'Photo, Product 1 and Product 2')
        self.assertNotIn('Product 1', action['texts'][0])
        payload = deterministic_story(options)
        self.assertEqual(validate_story(payload, options), payload)
        payload['actions'][0]['finding_ids'].pop()
        with self.assertRaises(ValueError):
            validate_story(payload, options)

    def test_same_metric_different_certainty_remains_separate(self):
        result = report((2, 9))
        change(result, 'photo.blur', 'Manual Review')
        change(result, 'product-2.blur', 'Fail')
        change(result, 'product-9.blur', 'Fail')
        actions = story_options(result)['actions']
        self.assertEqual([a['kind'] for a in actions], ['confirmed', 'incomplete'])
        self.assertEqual(actions[0]['scope'], 'Product 1 and Product 2')
        self.assertEqual(actions[1]['scope'], 'Photo')

    def test_grouping_leaves_room_for_three_distinct_improvements(self):
        result = report((2, 9))
        for identity in ('photo.blur', 'product-2.blur', 'product-9.blur',
                         'photo.noise', 'product-9.noise', 'photo.contrast'):
            change(result, identity, 'Fail')
        actions = story_options(result)['actions']
        self.assertEqual(len(actions), 3)
        self.assertEqual([a['id'].rsplit('.', 1)[1] for a in actions], ['blur', 'noise', 'contrast'])

    def test_legacy_small_in_frame_depends_on_unresolved_selection_only(self):
        result = report()
        change(result, 'photo.group_size', 'Manual Review', reason_code='small_in_frame')
        change(result, 'photo.coverage', 'Manual Review')
        options = story_options(result)
        self.assertEqual(len(options['actions']), 1)
        self.assertEqual(options['actions'][0]['finding_ids'], ['photo.coverage', 'photo.group_size'])
        self.assertIn('intended products', options['summary'][0])
        change(result, 'photo.coverage', 'Pass')
        self.assertEqual(story_options(result)['actions'][0]['id'], 'photo.group_size')

    def test_positive_strengths_are_distinct_and_product_detail_is_natural(self):
        result = report((8, 3))
        for check in result['checks']:
            if check['identifier'] not in {'product-3.blur', 'product-8.blur', 'product-3.detail'}:
                check['status'] = 'Manual Review'
        options = story_options(result)
        self.assertEqual(len(options['good']), 2)
        self.assertEqual({g['id'].rsplit('.', 1)[1] for g in options['good']}, {'blur', 'detail'})
        detail = next(g for g in options['good'] if g['id'].endswith('.detail'))
        self.assertEqual(detail['texts'][0], 'Product 1 is large enough in the image to inspect.')

    def test_concrete_uncertainties_and_resolution_action(self):
        for key, phrase in [('mask_completeness', 'outline may miss part'), ('edge', 'outside the frame'),
                            ('blur', 'could not judge focus'), ('clipping', 'bright and dark')]:
            result = report()
            identity = 'product-1.' + key
            check = change(result, identity, 'Manual Review')
            self.assertIn(phrase, friendly_finding(check))
            self.assertIn(phrase, story_options(result)['summary'][0])
        change(result, 'photo.resolution', 'Fail')
        action = story_options(result)['actions'][0]
        self.assertEqual(action['target'], 'evidence')
        self.assertEqual(action['title'], 'Use a larger original photo')
        self.assertIn('same small file', action['texts'][0])

    def test_disabled_and_review_group_routes_to_reinspection(self):
        result = report((2, 9))
        change(result, 'photo.blur', 'Manual Review')
        change(result, 'product-2.blur', 'Disabled')
        change(result, 'product-9.blur', 'Manual Review')
        action = story_options(result)['actions'][0]
        self.assertEqual(action['target'], 'reinspect')
        self.assertEqual(len(action['finding_ids']), 3)

    def test_card_wording_does_not_promote_uncertainty_or_mask_guarantees(self):
        for status in ('Manual Review', 'Disabled', 'Unassessed'):
            self.assertIn('does not confirm', friendly_finding({'identifier': 'product-7.blur', 'status': status}))
        self.assertIn('not verified', friendly_finding({'identifier': 'product-1.mask_completeness', 'status': 'Pass'}))
        self.assertIn('cannot guarantee', friendly_finding({'identifier': 'product-1.edge', 'status': 'Pass'}))
        for key in ('blur', 'noise', 'resolution', 'edge', 'clipping', 'mask_completeness',
                    'contrast', 'group_size', 'group_margins', 'group_placement', 'coverage'):
            self.assertTrue(friendly_finding({'identifier': 'photo.' + key, 'status': 'Fail'}))

    def test_alternatives_and_reordered_good_are_valid(self):
        result = report()
        change(result, 'photo.blur', 'Manual Review')
        options = story_options(result)
        payload = deterministic_story(options)
        payload['headline'] = options['headline'][1]
        payload['summary'] = options['summary'][1]
        for key in ('good', 'actions'):
            for item, approved in zip(payload[key], options[key]):
                item['text'] = approved['texts'][1]
        payload['good'].reverse()
        self.assertEqual(validate_story(payload, options), payload)

    def test_invalid_payloads_rejected(self):
        result = report((2, 9))
        change(result, 'product-2.blur', 'Fail')
        change(result, 'photo.coverage', 'Manual Review')
        options = story_options(result)
        valid = deterministic_story(options)
        changes = [
            lambda p: p.update(extra=True),
            lambda p: p.pop('summary'),
            lambda p: p.update(headline='Your photo looks good.'),
            lambda p: p.update(summary='Buy a better camera.'),
            lambda p: p['actions'].pop(),
            lambda p: p['actions'].append(copy.deepcopy(p['actions'][0])),
            lambda p: p['actions'].reverse(),
            lambda p: p['actions'][0].update(text='Product 2: use a sharper photo.'),
            lambda p: p['actions'][0].update(finding_ids=['product-9.blur']),
            lambda p: p['actions'][0].update(scope='Product 2'),
            lambda p: p['actions'][0].update(id=[]),
            lambda p: p['good'].__setitem__(1, copy.deepcopy(p['good'][0])),
            lambda p: p['good'].append(p['actions'].pop()),
            lambda p: p.update(actions=None),
        ]
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                payload = copy.deepcopy(valid)
                mutate(payload)
                with self.assertRaises(ValueError):
                    validate_story(payload, options)


if __name__ == '__main__':
    unittest.main()
