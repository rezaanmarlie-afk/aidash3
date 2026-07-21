from app.main import _initiative_yield_completion


def test_compact_runtime_initiative_retains_dates_for_yield():
    result = {
        "initiative": {
            "key": "NMGOS-3264",
            "status": "Done",
            "fields": {
                "customfield_10023": "2026-06-12",
                "resolutiondate": "2026-06-13T11:55:25.820+0200",
                "duedate": "2026-06-05",
                "statuscategorychangedate": "2026-06-13T11:55:18.109+0200",
            },
            "target_end_date": "2026-06-12",
            "_target_end_field_id": "customfield_10023",
            "_target_end_field_name": "Target end",
        },
        "epics": [],
        "direct_stories": [],
        "additional_descendants": [],
    }
    completion = _initiative_yield_completion(result)
    assert completion["completed"] is True
    assert completion["target_end_date"] == "2026-06-12"
    assert completion["resolution_date"] == "2026-06-13"
    assert completion["allowed_completion_date"] == "2026-06-14"
