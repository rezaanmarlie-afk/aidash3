from app.main import _apply_dependency_business_impact_only_note


def test_dependency_full_field_fallback_is_disabled_for_scan_performance():
    assert _apply_dependency_business_impact_only_note() == 'disabled; Business Impact fields only'
