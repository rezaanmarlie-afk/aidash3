from app.compliance import ComplianceEngine, description_section, REQUIREMENT_HEADING_ALIASES


def _issue(description: str) -> dict:
    return {
        "key": "NMGOS-999",
        "fields": {
            "summary": "Alias test",
            "description": description,
            "issuetype": {"name": "Story"},
            "status": {"name": "Ready"},
            "issuelinks": [],
            "timetracking": {"originalEstimateSeconds": 3600},
        },
    }


def test_uppercase_dor_and_dod_description_headings_are_recognised():
    engine = ComplianceEngine({}, ["NMGOS"], allow_description_fallback=True)
    result = engine.evaluate_issue(_issue(
        "DOR:\nScope and design are approved.\n\n"
        "DOD:\nDeployment, monitoring and handover are complete.\n\n"
        "Acceptance Criteria:\nThe service passes end-to-end validation.\n\n"
        "Dependencies:\nNo known dependencies"
    ))
    checks = {c["key"]: c for c in result["checks"]}
    assert checks["dor"]["passed"] is True
    assert checks["dod"]["passed"] is True
    assert checks["acceptance_criteria"]["passed"] is True


def test_markdown_and_inline_acronym_headings_are_recognised():
    description = (
        "- **DoR:** Architecture and scope approved\n"
        "- **DoD:** Production validation and handover complete\n"
        "- **AC:** All acceptance tests pass"
    )
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dor"]) == "Architecture and scope approved"
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dod"]) == "Production validation and handover complete"
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["acceptance_criteria"]) == "All acceptance tests pass"


def test_empty_dor_does_not_absorb_dod_evidence():
    description = (
        "DOR:\n"
        "DOD:\nDeployment and handover are complete.\n"
        "Acceptance Criteria:\nTests pass."
    )
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dor"]) == ""
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dod"]) == "Deployment and handover are complete."


def test_jira_table_heading_form_is_recognised():
    description = "| DOR | Technical breakdown completed |\n| DOD | Release evidence attached |"
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dor"]) == "Technical breakdown completed"
    assert description_section(description, REQUIREMENT_HEADING_ALIASES["dod"]) == "Release evidence attached"
