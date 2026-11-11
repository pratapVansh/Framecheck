"""User-facing stories must stay natural, scoped, and separate from inspection decisions."""
import copy
import json

from inspector import auto_inspect as auto, modern_ui, narration
from inspector.story import story_options, deterministic_story
from test_one_click import normal_image, unit_policy, record
from test_score_intent import scene
from test_narration import story_response, response_for


def test_all_clear_says_photo_looks_good_and_hides_mechanics(normal_image, unit_policy):
    result = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    before = result.to_json()
    html = modern_ui.render_result({**record(result), 'filename': 'Ready photo.png'})
    main, details = html.split('<details class="review-details"', 1)
    assert 'Your photo looks good.' in main
    assert 'data-quality-score>100' in main and 'Pass' in main
    assert 'What works' in main
    assert 'class="action-item"' not in main
    assert all(text not in main for text in ['points deducted', 'Checks completed', 'profile_sha256', 'scoring_version', 'DIP evidence determines'])
    assert 'score-explanation' in details and 'profile_sha256' in details
    assert result.to_json() == before


def test_uncertainty_is_not_a_defect_or_an_all_clear(normal_image, unit_policy):
    result = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    check = next(c for c in result.checks if c.identifier == 'photo.blur')
    check.status = 'Manual Review'; check.reason_code = 'calibration_boundary'
    before = copy.deepcopy(result.to_dict())
    story = deterministic_story(story_options(before))
    assert 'Your photo looks good.' != story['headline']
    assert all(action['kind'] == 'incomplete' for action in story_options(before)['actions'])
    html = modern_ui.render_result({**record(result), 'filename': 'Focus review.png'})
    overview = html.split('<details class="review-details"', 1)[0]
    assert 'Needs a closer look' in overview and 'To improve:' not in overview
    assert result.to_dict() == before


def test_multi_product_focus_is_one_scoped_action_without_changing_score(monkeypatch):
    result = scene(monkeypatch, [(140, 230, 350, 520), (180, 830, 260, 170),
                                (700, 270, 320, 480), (730, 830, 240, 170)])
    for check in result.checks:
        if check.identifier in {'photo.blur', 'product-1.blur', 'product-3.blur'}:
            check.status = 'Manual Review'; check.reason_code = 'calibration_boundary'
    options = story_options(result.to_dict())
    focus = [a for a in options['actions'] if any(i.endswith('.blur') for i in a['finding_ids'])]
    assert len(focus) == 1
    assert set(focus[0]['finding_ids']) == {'photo.blur', 'product-1.blur', 'product-3.blur'}
    assert 'Product 1' in focus[0]['scope'] and 'Product 2' in focus[0]['scope']
    assert len(options['actions']) <= 3
    assert result.score()['confirmed_deductions'] == 0


def test_groq_changes_wording_only_and_invalid_claim_uses_full_fallback(monkeypatch, tmp_path, normal_image, unit_policy):
    result = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy).to_dict()
    original = copy.deepcopy(result)
    monkeypatch.setattr(narration, 'ENV_FILE', tmp_path / 'missing')
    monkeypatch.setenv('GROQ_API_KEY', 'test-only-key')
    requests = []
    def valid(request, timeout):
        requests.append(json.loads(request.data))
        return story_response(request)
    narration._explain.cache_clear()
    monkeypatch.setattr(narration.urllib.request, 'urlopen', valid)
    valid_result = narration.explain(result)
    assert valid_result['source'] == 'groq_validated'
    assert valid_result['story'] != deterministic_story(story_options(result))
    assert 'test-only-key' not in json.dumps(requests)
    assert result == original
    tampered = copy.deepcopy(valid_result['story'])
    tampered['headline'] = 'Guaranteed marketplace approval.'
    narration._explain.cache_clear()
    monkeypatch.setattr(narration.urllib.request, 'urlopen', lambda *a, **k: response_for({'story': tampered}))
    fallback = narration.explain(result)
    assert fallback['source'] == 'local'
    assert fallback['story'] == deterministic_story(story_options(result))
    assert fallback['items'] == narration.approved_wording(result)
    assert result == original
    narration._explain.cache_clear()
