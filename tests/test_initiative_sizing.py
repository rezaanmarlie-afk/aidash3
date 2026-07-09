from app.main import classify_initiative_size, apply_initiative_size


def test_initiative_size_uses_rolled_up_story_points():
    thresholds = [
        {'code': 'XS', 'label': 'Extra Small', 'max_points': 20},
        {'code': 'S', 'label': 'Small', 'max_points': 50},
        {'code': 'M', 'label': 'Medium', 'max_points': 100},
        {'code': 'L', 'label': 'Large', 'max_points': 200},
        {'code': 'XL', 'label': 'Extra Large', 'max_points': 400},
    ]
    result = {'story_points_total': 36, 'initiative': {}}
    sized = apply_initiative_size(result, thresholds)
    assert sized['initiative_size_code'] == 'S'
    assert '36 rolled-up SP' in sized['initiative_size_basis']
    assert sized['initiative']['initiative_size_code'] == 'S'


def test_initiative_size_unestimated_and_overflow():
    thresholds = [{'code': 'XS', 'label': 'Extra Small', 'max_points': 20}]
    assert classify_initiative_size(0, thresholds)['code'] == 'Unestimated'
    assert classify_initiative_size(21, thresholds)['code'] == 'XXL'
