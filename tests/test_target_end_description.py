from app.main import _initiative_target_date


def test_target_end_from_plain_description():
    issue = {"fields": {"description": "Business Impact\nNo dependencies\nTarget end: 2026-07-20\nExpected Business Value\nValue"}}
    parsed, source, raw = _initiative_target_date(issue)
    assert parsed.isoformat() == "2026-07-20"
    assert source == "Description / ADF: Target end"


def test_target_end_from_adf_description():
    issue = {"fields": {"description": {
        "type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Target end: 2026-07-20"}]}
        ]
    }}}
    parsed, source, raw = _initiative_target_date(issue)
    assert parsed.isoformat() == "2026-07-20"
    assert source == "Description / ADF: Target end"
