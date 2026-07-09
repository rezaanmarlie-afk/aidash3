import hashlib
import json
import re
from dataclasses import dataclass, asdict
from typing import Any


NONE_PATTERNS = {
    'none', 'n/a', 'na', 'not applicable', 'no dependency', 'no dependencies',
    'no known dependency', 'no known dependencies', 'nil'
}


ADF_BLOCK_TYPES = {
    'doc', 'paragraph', 'heading', 'blockquote', 'codeBlock', 'panel',
    'bulletList', 'orderedList', 'listItem', 'table', 'tableRow',
    'tableHeader', 'tableCell', 'rule', 'mediaGroup', 'mediaSingle',
}


def _render_adf_node(value: Any) -> str:
    """Render Jira Cloud Atlassian Document Format while preserving blocks.

    Jira Cloud REST API v3 returns Description and rich-text custom fields as
    ADF JSON. Compliance headings must remain on separate lines; flattening all
    ADF nodes with spaces makes headings such as Definition of Ready (DoR) and
    Definition of Done (DoD) impossible to detect.
    """
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ''.join(_render_adf_node(item) for item in value)
    if not isinstance(value, dict):
        return str(value)

    node_type = str(value.get('type') or '')
    attrs = value.get('attrs') or {}

    if node_type == 'text':
        return str(value.get('text') or '')
    if node_type == 'hardBreak':
        return '\n'
    if node_type == 'rule':
        return '\n'
    if node_type == 'mention':
        return str(attrs.get('text') or attrs.get('displayName') or attrs.get('id') or '')
    if node_type == 'emoji':
        return str(attrs.get('text') or attrs.get('shortName') or '')
    if node_type in {'inlineCard', 'blockCard'}:
        return str(attrs.get('url') or '')
    if node_type == 'status':
        return str(attrs.get('text') or '')

    children = value.get('content') or []
    rendered_children = [_render_adf_node(child) for child in children]

    if node_type == 'doc':
        return '\n'.join(part for part in rendered_children if part != '')
    if node_type in {'bulletList', 'orderedList', 'listItem', 'table'}:
        return '\n'.join(part for part in rendered_children if part != '')
    if node_type == 'tableRow':
        return ' | '.join(part.strip() for part in rendered_children if part.strip())
    if node_type in {'tableHeader', 'tableCell'}:
        return ' '.join(part.strip() for part in rendered_children if part.strip())

    # Paragraphs/headings contain inline nodes and therefore must not insert
    # spaces between adjacent marked text fragments.
    if node_type in ADF_BLOCK_TYPES:
        return ''.join(rendered_children)

    return ''.join(rendered_children)


def rich_text_to_text(value: Any) -> str:
    """Return readable plain text from Jira string, ADF, or field values."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and (
        value.get('type') in ADF_BLOCK_TYPES or
        ('content' in value and isinstance(value.get('content'), list))
    ):
        text = _render_adf_node(value)
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = re.sub(r'\n[ \t]+', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    return flatten_text(value)


def flatten_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ' '.join(filter(None, (flatten_text(v) for v in value))).strip()
    if isinstance(value, dict):
        if value.get('type') in ADF_BLOCK_TYPES:
            return rich_text_to_text(value)
        preferred = ['value', 'name', 'displayName', 'summary', 'text']
        pieces = [flatten_text(value.get(k)) for k in preferred if k in value]
        if not any(pieces) and 'content' in value:
            pieces.append(rich_text_to_text(value))
        return ' '.join(filter(None, pieces)).strip()
    return str(value).strip()


def normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().lower()


REQUIREMENT_HEADING_ALIASES = {
    'dor': [
        'Definition of Ready', 'Definition-of-Ready',
        'DoR', 'DOR',
        'Definition of Ready (DoR)', 'DOR (Definition of Ready)',
    ],
    'dod': [
        'Definition of Done', 'Definition-of-Done',
        'DoD', 'DOD',
        'Definition of Done (DoD)', 'DOD (Definition of Done)',
    ],
    'acceptance_criteria': [
        'Acceptance Criteria', 'Acceptance Criterion', 'AC',
    ],
    'dependencies': [
        'Dependencies', 'Known Dependencies', 'Known Dependency',
    ],
    'story_estimation': [
        'Story Estimation', 'Story Sizing', 'Estimation', 'Sizing', 'Story Points',
    ],
}


def _unique_headings(groups: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for heading in group:
            key = normalized(heading)
            if key and key not in seen:
                seen.add(key)
                result.append(heading)
    return result


ALL_DESCRIPTION_HEADINGS = _unique_headings(list(REQUIREMENT_HEADING_ALIASES.values()))
DESCRIPTION_BOUNDARY_HEADINGS = _unique_headings([
    ALL_DESCRIPTION_HEADINGS,
    ['Summary', 'Context', 'Other Information', 'Background', 'Description', 'Objective', 'Objectives'],
])


def _heading_line(line: str, headings: list[str]) -> tuple[bool, str]:
    '''Return whether a line is a recognised heading and any inline evidence.

    Supported examples include DOR, DoR:, **DOD:**, - Definition of Ready,
    h3. Definition of Done, and Jira/Markdown table rows.
    '''
    if not line or not headings:
        return False, ''

    aliases = {normalized(h) for h in headings if normalized(h)}
    cleaned = str(line).replace('**', '').replace('__', '').replace('`', '').strip()

    # Jira/Markdown table form: | DOR | Ready evidence |
    if cleaned.startswith('|') and cleaned.endswith('|'):
        cells = [c.strip() for c in cleaned.strip('|').split('|')]
        if cells and normalized(cells[0]) in aliases:
            return True, ' | '.join(c for c in cells[1:] if c).strip()

    alternatives = '|'.join(re.escape(h) for h in sorted(headings, key=len, reverse=True))
    pattern = re.compile(
        rf'''(?ix)^\s*
        (?:[-*+•>]\s*)?
        (?:\#{{1,6}}\s*)?
        (?:h[1-6]\.\s*)?
        (?:\d+[.)]\s*)?
        (?P<heading>{alternatives})
        \s*(?:(?P<separator>:|[-–—=])\s*(?P<inline>.*))?\s*$'''
    )
    match = pattern.match(cleaned)
    if not match:
        return False, ''
    return True, (match.group('inline') or '').strip()


def description_section(
    description: str,
    headings: list[str],
    boundary_headings: list[str] | None = None,
) -> str:
    '''Extract evidence beneath a clearly labelled description heading.

    Matching is case-insensitive, so DoR/DOR and DoD/DOD are equivalent. The
    section stops at the next known compliance heading, including acronym-only
    headings, preventing evidence from one section being counted for another.
    '''
    if not description:
        return ''

    lines = str(description).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    boundaries = boundary_headings or DESCRIPTION_BOUNDARY_HEADINGS

    for index, line in enumerate(lines):
        matched, inline = _heading_line(line, headings)
        if not matched:
            continue

        pieces: list[str] = [inline] if inline else []
        for following in lines[index + 1:]:
            is_boundary, _ = _heading_line(following, boundaries)
            if is_boundary:
                break
            pieces.append(following)

        evidence = '\n'.join(pieces).strip()
        if normalized(evidence):
            return evidence

    return ''


@dataclass
class CheckResult:
    key: str
    label: str
    passed: bool
    evidence: str
    remediation: str
    applicable: bool = True
    excluded: bool = False


DEFAULT_FIELD_NAMES = {
    'dor': REQUIREMENT_HEADING_ALIASES['dor'],
    'dod': REQUIREMENT_HEADING_ALIASES['dod'],
    'acceptance_criteria': ['Acceptance Criteria', 'Acceptance criteria'],
    'dependencies': ['Dependencies', 'Known Dependencies', 'Known dependencies'],
    'story_points': ['Story Points', 'Story point estimate', 'Story Points Estimate'],
    'business_impact': ['Business Impact', 'Business impact', 'Business Impact / Value', 'Impact'],
    'squad': ['Squad', 'Delivery Squad', 'ASOC Squad'],
}


def _point_value(value: float | int | None) -> float | int:
    """Return story point totals without noisy trailing decimals."""
    number = round(float(value or 0), 2)
    return int(number) if number.is_integer() else number


def _explicit_no_dependencies(text: str, allow_bare_none: bool = True) -> bool:
    """Return True when text clearly declares that dependencies do not exist.

    Dedicated dependency fields often contain only "None" or "N/A". Wider
    narrative fields such as Business Impact must explicitly mention
    dependencies so that a normal "None" business-impact value is not mistaken
    for a dependency declaration.

    Manager rule: any Business Impact value that contains a clear phrase such as
    "No dependencies" must pass the Known Dependencies check, even when the
    same field continues with additional business-impact text. Examples that
    must pass include "No dependencies and use cases in place",
    "No dependencies - create", and "There are no known dependencies, but ...".
    """
    cleaned = normalized(text)
    if not cleaned:
        return False
    if allow_bare_none and cleaned in NONE_PATTERNS:
        return True

    # Fast-path the exact manager wording before applying the broader patterns.
    # This deliberately allows additional text before or after the declaration.
    compact = re.sub(r'[^a-z0-9]+', ' ', cleaned).strip()
    explicit_phrases = [
        'no dependencies', 'no dependency', 'no known dependencies',
        'no known dependency', 'no dependant', 'no dependants',
        'no dependancies', 'no dependancy', 'no deps',
    ]
    if any(phrase in compact for phrase in explicit_phrases):
        return True

    patterns = [
        r'\bno\s+(?:known\s+|external\s+|internal\s+|business\s+|technical\s+)?dependenc(?:y|ies)\b',
        r'\bno\s+deps?\b',
        r'\bno\s+dependanc(?:y|ies)\b',
        r'\bdependenc(?:y|ies)\s*(?:-|–|—|:|=)?\s*(?:none|nil|n/a|na|not applicable|no)\b',
        r'\bdependanc(?:y|ies)\s*(?:-|–|—|:|=)?\s*(?:none|nil|n/a|na|not applicable|no)\b',
        r'\bnone\s*(?:-|–|—|:|=)?\s*(?:known\s+)?dependenc(?:y|ies)\b',
        r'\bwithout\s+(?:any\s+)?dependenc(?:y|ies)\b',
    ]
    return any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in patterns)

class ComplianceEngine:
    def __init__(
        self,
        field_map: dict[str, str],
        internal_projects: list[str],
        allow_description_fallback: bool = True,
        excluded_criteria: set[str] | list[str] | None = None,
        additional_criteria: list[dict[str, Any]] | None = None,
    ):
        self.field_map = field_map
        self.internal_projects = {p.strip().upper() for p in internal_projects if p.strip()}
        self.allow_description_fallback = allow_description_fallback
        self.excluded_criteria = {str(key).strip() for key in (excluded_criteria or []) if str(key).strip()}
        self.additional_criteria = [dict(item) for item in (additional_criteria or []) if item.get('field_id')]
        self.story_point_field_ids = self._configured_story_point_field_ids(field_map)

    def _apply_exclusion(self, check: CheckResult) -> CheckResult:
        if check.key not in self.excluded_criteria:
            return check
        return CheckResult(
            key=check.key,
            label=check.label,
            passed=True,
            evidence='Excluded from this compliance scan by the manager.',
            remediation='',
            applicable=False,
            excluded=True,
        )

    @staticmethod
    def _configured_story_point_field_ids(field_map: dict[str, Any]) -> list[str]:
        """Return every Story Points field ID that should be tested.

        Jira sites often expose more than one similarly named Story Points
        field. A saved mapping can be technically valid but belong to another
        project/team, which makes every roll-up look like zero. The engine
        therefore checks the explicitly mapped field first and then falls back
        to any candidate Story Points fields discovered from Jira metadata.
        """
        candidates: list[str] = []
        primary = field_map.get('story_points')
        if isinstance(primary, str) and primary.strip():
            candidates.append(primary.strip())
        for key in ('story_points_candidates', '_story_points_candidates'):
            extra = field_map.get(key)
            if isinstance(extra, str):
                candidates.extend(part.strip() for part in extra.split(',') if part.strip())
            elif isinstance(extra, (list, tuple, set)):
                candidates.extend(str(part).strip() for part in extra if str(part).strip())
        return list(dict.fromkeys(candidates))

    def _field(self, issue: dict, logical_name: str) -> Any:
        field_id = self.field_map.get(logical_name)
        return issue.get('fields', {}).get(field_id) if isinstance(field_id, str) and field_id else None

    def _field_text_candidates(self, issue: dict, logical_name: str) -> list[dict[str, str]]:
        """Return readable values for a logical field from every candidate source.

        Jira estates often contain duplicate project-scoped custom fields. A
        saved mapping may therefore point at a valid but irrelevant/empty field,
        while the correct project field is available through the scan candidate
        list or late issue-metadata enrichment. Business Impact is especially
        important because managers use values such as "No dependencies and ..."
        to satisfy the Known Dependencies control.
        """
        fields = issue.get('fields', {}) or {}
        results: list[dict[str, str]] = []
        seen: set[str] = set()

        def add_value(field_id: str, value: Any, source: str = ''):
            fid = str(field_id or '').strip()
            if not fid or fid in seen:
                return
            seen.add(fid)
            text = flatten_text(value)
            if text:
                results.append({'field_id': fid, 'source': source or fid, 'text': text})

        primary = str(self.field_map.get(logical_name) or '').strip()
        if primary:
            add_value(primary, fields.get(primary), primary)

        candidate_key = f'{logical_name}_candidates'
        candidates = self.field_map.get(candidate_key, [])
        if isinstance(candidates, str):
            candidate_ids = [part.strip() for part in candidates.split(',') if part.strip()]
        elif isinstance(candidates, (list, tuple, set)):
            candidate_ids = [str(part).strip() for part in candidates if str(part).strip()]
        else:
            candidate_ids = []
        for field_id in candidate_ids:
            add_value(field_id, fields.get(field_id), field_id)

        # v1.19: late Jira issue-metadata enrichment can attach populated
        # Business Impact candidates that were not in the global field catalogue
        # or not selected in Settings. Include them after configured mappings.
        if logical_name == 'business_impact':
            for item in issue.get('_business_impact_candidates', []) or []:
                if not isinstance(item, dict):
                    continue
                field_id = str(item.get('field_id') or '').strip()
                name = str(item.get('name') or field_id).strip()
                add_value(field_id, item.get('value'), name)

            # v1.20 safety net: some Jira projects expose the Business Impact
            # field under a workspace-specific custom field that is not named in
            # the global field catalogue and may not be included in saved
            # settings. If any returned custom field explicitly contains
            # "No dependencies" with additional words, include it as a
            # dependency declaration candidate. This is deliberately restricted
            # to explicit no-dependency wording; a plain "None" still does
            # not pass for Business Impact.
            for field_id, raw in fields.items():
                fid = str(field_id)
                if fid in seen or not fid.startswith('customfield_'):
                    continue
                text = flatten_text(raw)
                if _explicit_no_dependencies(text, allow_bare_none=False):
                    add_value(fid, raw, fid)

        return results

    def _field_evidence(self, issue: dict, logical_name: str) -> str:
        """Return readable evidence for a logical field.

        For Business Impact, prefer any candidate that explicitly declares no
        dependencies, even when another duplicate Business Impact field is also
        populated. This avoids failing Known Dependencies because Jira returned
        a different duplicate field first.
        """
        candidates = self._field_text_candidates(issue, logical_name)
        if logical_name == 'business_impact':
            for candidate in candidates:
                if _explicit_no_dependencies(candidate['text'], allow_bare_none=False):
                    return candidate['text']
        return candidates[0]['text'] if candidates else ''

    def _explicit_no_dependency_field_evidence(self, issue: dict) -> tuple[str, str]:
        """Find an explicit no-dependency declaration in Business Impact.

        Returns (evidence_text, source_name). The source name is used in the
        detail view and exports so the manager can see why the dependency check
        passed.
        """
        for candidate in self._field_text_candidates(issue, 'business_impact'):
            text = candidate['text']
            if _explicit_no_dependencies(text, allow_bare_none=False):
                return text, candidate.get('source') or 'Business Impact field'
        return '', ''

    def _story_points_raw(self, issue: dict) -> tuple[Any, str]:
        fields = issue.get('fields', {}) or {}
        zero_candidate: tuple[Any, str] | None = None
        non_numeric_candidate: tuple[Any, str] | None = None

        def consider(raw: Any, field_id: str) -> tuple[Any, str] | None:
            nonlocal zero_candidate, non_numeric_candidate
            number = self._number(raw)
            if number is not None:
                if number > 0:
                    return raw, field_id
                if zero_candidate is None:
                    zero_candidate = (raw, field_id)
            elif flatten_text(raw):
                if non_numeric_candidate is None:
                    non_numeric_candidate = (raw, field_id)
            return None

        seen: set[str] = set()
        for field_id in self.story_point_field_ids:
            if field_id in seen or field_id not in fields:
                continue
            seen.add(field_id)
            found = consider(fields.get(field_id), field_id)
            if found:
                return found

        # Jira can expose different estimation fields per board/team-managed
        # workspace. The scan enriches issues with candidate fields discovered
        # from the actual issue metadata when the global field catalogue is not
        # sufficient. Treat those as a second-priority source so an explicit
        # manager mapping still wins when both are populated.
        for candidate in issue.get('_story_point_candidates', []) or []:
            if not isinstance(candidate, dict):
                continue
            field_id = str(candidate.get('field_id') or '').strip()
            if not field_id or field_id in seen:
                continue
            seen.add(field_id)
            found = consider(candidate.get('value'), field_id)
            if found:
                return found

        return zero_candidate or non_numeric_candidate or (None, '')

    def _story_points_value(self, issue: dict) -> float:
        """Extract the Story Points value from the mapped or discovered fields.

        Jira implementations differ: some store story points only on Stories,
        while others allow sizing at Epic or Initiative level. The roll-up keeps
        the issue's own value separate from descendant totals so there is no
        ambiguity in the dashboard and exports.
        """
        raw, _field_id = self._story_points_raw(issue)
        number = self._number(raw)
        return float(number or 0.0)

    def _text_requirement(self, issue: dict, logical_name: str, label: str, headings: list[str]) -> CheckResult:
        evidence = self._field_evidence(issue, logical_name)
        source = 'Dedicated Jira field'
        if not evidence and self.allow_description_fallback:
            description = rich_text_to_text(issue.get('fields', {}).get('description'))
            evidence = description_section(description, headings)
            source = 'Description section'
        passed = bool(normalized(evidence))
        return CheckResult(
            key=logical_name,
            label=label,
            passed=passed,
            evidence=f'{source}: {evidence[:240]}' if passed else 'No usable evidence found',
            remediation=f'Complete the {label} field or a clearly labelled {label} section in the description.',
        )

    def _dependencies(self, issue: dict, issue_type: str) -> CheckResult:
        declaration = self._field_evidence(issue, 'dependencies')
        declaration_source = 'Dedicated Jira field'
        if not declaration and self.allow_description_fallback:
            description = rich_text_to_text(issue.get('fields', {}).get('description'))
            declaration = description_section(
                description, REQUIREMENT_HEADING_ALIASES['dependencies']
            )
            declaration_source = 'Description section'

        links = issue.get('fields', {}).get('issuelinks') or []
        linked_projects = set()
        linked_keys = []
        for link in links:
            linked = link.get('outwardIssue') or link.get('inwardIssue') or {}
            key = linked.get('key', '')
            if key:
                linked_keys.append(key)
                linked_projects.add(key.split('-', 1)[0].upper())

        business_impact, business_impact_source = self._explicit_no_dependency_field_evidence(issue)
        business_impact_declares_no_dependencies = bool(business_impact)

        declaration_norm = normalized(declaration)
        explicitly_none = _explicit_no_dependencies(declaration, allow_bare_none=True)
        has_declaration = bool(declaration_norm)
        has_links = bool(linked_keys)

        # Manager rule: when the Business Impact field explicitly says there
        # are no dependencies, the Known Dependencies control is satisfied.
        # This is an intentional override because ASOC initiatives sometimes
        # document dependency absence in Business Impact rather than a dedicated
        # dependency field. Bare values such as "None" are still not accepted
        # here; the text must mention dependencies.
        if business_impact_declares_no_dependencies:
            passed = True
            raw_source = business_impact_source or ''
            source_label = 'Business Impact field' if not raw_source or raw_source.startswith('customfield_') else f'Business Impact field ({raw_source})'
            evidence = f'{source_label}: dependencies explicitly declared as: {business_impact}'
        elif explicitly_none:
            passed = True
            evidence = f'{declaration_source}: dependencies explicitly declared as: {declaration}'
        elif has_declaration and has_links:
            if issue_type == 'story' and self.internal_projects:
                internal_links = sorted(linked_projects & self.internal_projects)
                passed = bool(internal_links)
                evidence = (
                    f'{declaration_source}: {declaration[:160]}; linked tickets: {", ".join(linked_keys)}; '
                    f'internal squad project match: {", ".join(internal_links) or "none"}'
                )
            else:
                passed = True
                evidence = f'{declaration_source}: {declaration[:160]}; linked tickets: {", ".join(linked_keys)}'
        elif has_declaration and not has_links:
            passed = False
            evidence = f'{declaration_source}: dependency is declared but no Jira issue is linked: {declaration[:200]}'
        elif has_links and not has_declaration:
            passed = False
            evidence = f'Linked tickets exist ({", ".join(linked_keys)}) but the Dependencies field is not completed.'
        else:
            passed = False
            evidence = 'Dependencies are neither explicitly declared as none nor documented and linked.'

        return CheckResult(
            key='dependencies', label='Known dependencies', passed=passed, evidence=evidence,
            remediation='State “No known dependencies” or describe each dependency and link the relevant Jira ticket. Stories must link to an ASOC-internal squad project when applicable.'
        )

    def _estimate(self, issue: dict, issue_type: str) -> CheckResult:
        if issue_type != 'story':
            return CheckResult(
                key='story_estimation', label='Story estimation / sizing', passed=True,
                evidence='Only applicable to Story-level work items.', remediation='', applicable=False
            )
        raw, story_points_field_id = self._story_points_raw(issue)
        original_estimate = ((issue.get('fields', {}).get('timetracking') or {}).get('originalEstimateSeconds'))
        numeric_points = self._number(raw) or 0.0
        passed = numeric_points > 0 or (original_estimate or 0) > 0
        evidence = (
            f'Story points: {_point_value(numeric_points)} from {story_points_field_id}' if numeric_points > 0 else (
                f'Original estimate: {original_estimate} seconds' if original_estimate else 'No story points or original estimate found'
            )
        )
        return CheckResult(
            key='story_estimation', label='Story estimation / sizing', passed=passed,
            evidence=evidence,
            remediation='Add story points or a non-zero original estimate before PI sign-off.'
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = flatten_text(value).replace(',', '').strip()
        if not text:
            return None
        match = re.search(r'-?\d+(?:\.\d+)?', text)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return normalized(flatten_text(value)) in {'true', 'yes', 'y', '1', 'approved', 'complete', 'completed'}

    def _custom_criterion(self, issue: dict, criterion: dict[str, Any], hierarchy_level: str) -> CheckResult:
        criterion_id = str(criterion.get('id') or criterion.get('field_id') or '').strip()
        key = f'custom:{criterion_id}'
        label = str(criterion.get('label') or criterion.get('field_name') or criterion.get('field_id') or 'Additional Jira field').strip()
        applies_to = str(criterion.get('applies_to') or 'all').strip().lower()
        if applies_to not in {'all', hierarchy_level}:
            return CheckResult(
                key=key,
                label=label,
                passed=True,
                evidence=f'Only applicable to {applies_to.replace("top_level", "top-level ticket")} work items.',
                remediation='',
                applicable=False,
            )

        field_id = str(criterion.get('field_id') or '').strip()
        raw = (issue.get('fields') or {}).get(field_id)
        text = flatten_text(raw)
        rule = str(criterion.get('rule') or 'required').strip().lower()
        expected = str(criterion.get('expected') or '').strip()
        expected_values = [normalized(item) for item in re.split(r'[,;\n]+', expected) if normalized(item)]
        actual_norm = normalized(text)
        passed = False
        rule_description = ''

        if rule == 'required':
            passed = bool(actual_norm)
            rule_description = 'must be populated'
        elif rule == 'equals':
            passed = bool(actual_norm) and actual_norm == normalized(expected)
            rule_description = f'must equal "{expected}"'
        elif rule == 'not_equals':
            passed = bool(actual_norm) and actual_norm != normalized(expected)
            rule_description = f'must not equal "{expected}"'
        elif rule == 'contains':
            passed = bool(actual_norm) and normalized(expected) in actual_norm
            rule_description = f'must contain "{expected}"'
        elif rule == 'one_of':
            passed = bool(actual_norm) and actual_norm in expected_values
            rule_description = f'must be one of: {expected}'
        elif rule == 'numeric_min':
            actual_number = self._number(raw)
            expected_number = self._number(expected)
            passed = actual_number is not None and expected_number is not None and actual_number >= expected_number
            rule_description = f'must be at least {expected}'
        elif rule == 'numeric_max':
            actual_number = self._number(raw)
            expected_number = self._number(expected)
            passed = actual_number is not None and expected_number is not None and actual_number <= expected_number
            rule_description = f'must be no more than {expected}'
        elif rule == 'boolean_true':
            passed = self._truthy(raw)
            rule_description = 'must be Yes / True / Complete'
        else:
            rule_description = f'uses unsupported rule: {rule}'

        field_name = str(criterion.get('field_name') or field_id)
        evidence = f'{field_name}: {text}' if text else f'{field_name}: no value found'
        remediation = f'Complete {field_name}; it {rule_description}.'
        return self._apply_exclusion(CheckResult(
            key=key,
            label=label,
            passed=passed,
            evidence=evidence[:500],
            remediation=remediation,
        ))

    def evaluate_issue(self, issue: dict, hierarchy_level: str = '') -> dict:
        issue_type_name = flatten_text((issue.get('fields', {}).get('issuetype') or {}).get('name')).lower()
        issue_type = 'initiative' if 'initiative' in issue_type_name else ('epic' if 'epic' in issue_type_name else ('story' if 'story' in issue_type_name else issue_type_name))
        if not hierarchy_level:
            hierarchy_level = 'story' if issue_type == 'story' else ('epic' if issue_type == 'epic' else 'top_level')
        checks = [
            self._apply_exclusion(self._text_requirement(
                issue, 'dor', 'Definition of Ready', REQUIREMENT_HEADING_ALIASES['dor']
            )),
            self._apply_exclusion(self._text_requirement(
                issue, 'dod', 'Definition of Done', REQUIREMENT_HEADING_ALIASES['dod']
            )),
            self._apply_exclusion(self._text_requirement(
                issue, 'acceptance_criteria', 'Acceptance Criteria',
                REQUIREMENT_HEADING_ALIASES['acceptance_criteria'],
            )),
            self._apply_exclusion(self._dependencies(issue, issue_type)),
            self._apply_exclusion(self._estimate(issue, issue_type)),
            *[self._custom_criterion(issue, criterion, hierarchy_level) for criterion in self.additional_criteria],
        ]
        applicable = [c for c in checks if c.applicable]
        applicable_count = len(applicable)
        passed_count = sum(1 for c in applicable if c.passed)
        passed = passed_count == applicable_count
        score = round((passed_count / applicable_count * 100), 1) if applicable_count else 100.0
        own_story_points = self._story_points_value(issue)
        return {
            'key': issue.get('key'),
            'summary': flatten_text(issue.get('fields', {}).get('summary')),
            'issue_type': issue_type_name.title() or 'Unknown',
            'status': flatten_text((issue.get('fields', {}).get('status') or {}).get('name')),
            'assignee': flatten_text(issue.get('fields', {}).get('assignee')),
            'story_points': _point_value(own_story_points),
            'own_story_points': _point_value(own_story_points),
            'story_points_from_epics': 0,
            'story_points_from_stories': 0,
            'rolled_story_points': _point_value(own_story_points),
            'total_story_points': _point_value(own_story_points),
            'passed': passed,
            'score': score,
            'passed_count': passed_count,
            'applicable_count': applicable_count,
            'checks': [asdict(c) for c in checks],
            'failure_count': applicable_count - passed_count,
        }

    def evaluate_tree(
        self,
        initiative: dict,
        epics: list[dict],
        stories_by_epic: dict[str, list[dict]],
        direct_stories: list[dict] | None = None,
    ) -> dict:
        initiative_result = self.evaluate_issue(initiative, 'top_level')
        epic_results = []
        story_results = []
        direct_story_results = [self.evaluate_issue(s, 'story') for s in (direct_stories or [])]
        for epic in epics:
            er = self.evaluate_issue(epic, 'epic')
            children = stories_by_epic.get(epic.get('key'), [])
            er['stories'] = [self.evaluate_issue(s, 'story') for s in children]
            child_story_points = sum(float(story.get('story_points') or 0) for story in er['stories'])
            er['story_points_from_stories'] = _point_value(child_story_points)
            er['rolled_story_points'] = _point_value(float(er.get('story_points') or 0) + child_story_points)
            er['total_story_points'] = er['rolled_story_points']
            epic_results.append(er)
            story_results.extend(er['stories'])

        initiative_own_story_points = float(initiative_result.get('story_points') or 0)
        epic_own_story_points = sum(float(epic.get('story_points') or 0) for epic in epic_results)
        nested_story_own_story_points = sum(float(story.get('story_points') or 0) for story in story_results)
        direct_story_own_story_points = sum(float(story.get('story_points') or 0) for story in direct_story_results)
        story_own_story_points = nested_story_own_story_points + direct_story_own_story_points
        rolled_story_points = initiative_own_story_points + epic_own_story_points + story_own_story_points
        initiative_result['story_points_from_epics'] = _point_value(epic_own_story_points)
        initiative_result['story_points_from_stories'] = _point_value(story_own_story_points)
        initiative_result['story_points_from_direct_stories'] = _point_value(direct_story_own_story_points)
        initiative_result['rolled_story_points'] = _point_value(rolled_story_points)
        initiative_result['total_story_points'] = _point_value(rolled_story_points)

        structural_checks = [
            self._apply_exclusion(CheckResult(
                key='has_epics', label='Top-level ticket has linked Epics', passed=bool(epics),
                evidence=f'{len(epics)} Epic(s) found' if epics else 'No Epics found beneath or linked to the top-level ticket',
                remediation='Create or link the delivery Epics to the top-level ticket.'
            )),
            self._apply_exclusion(CheckResult(
                key='epics_have_stories', label='Epics have Stories',
                passed=bool(epics) and all(stories_by_epic.get(e.get('key')) for e in epics),
                evidence='; '.join(f'{e.get("key")}: {len(stories_by_epic.get(e.get("key"), []))} Story(s)' for e in epics) or 'No Epic hierarchy found',
                remediation='Break every Epic down into linked Stories before manager sign-off.'
            )),
        ]
        all_items = [initiative_result, *epic_results, *story_results, *direct_story_results]
        compliant = all(i['passed'] for i in all_items) and all(c.passed for c in structural_checks)
        failures = sum(i['failure_count'] for i in all_items) + sum(1 for c in structural_checks if not c.passed)
        total_applicable = (
            sum(sum(1 for c in i['checks'] if c['applicable']) for i in all_items)
            + sum(1 for c in structural_checks if c.applicable)
        )
        total_passed = total_applicable - failures
        score = round((total_passed / total_applicable * 100), 1) if total_applicable else 100.0
        result = {
            'initiative': initiative_result,
            'epics': epic_results,
            'direct_stories': direct_story_results,
            'structural_checks': [asdict(c) for c in structural_checks],
            # Manager Sign-Off remains governed by the complete hierarchy.
            'compliant': compliant,
            'hierarchy_score': score,
            'score': score,  # Backwards-compatible alias used by older views/data.
            'hierarchy_passed_count': total_passed,
            'hierarchy_applicable_count': total_applicable,
            # The selected Jira ticket's own score is shown separately so a
            # healthy top-level ticket is not represented as a 10% ticket merely
            # because its linked delivery hierarchy is incomplete.
            'ticket_score': initiative_result['score'],
            'ticket_compliant': initiative_result['passed'],
            'ticket_failure_count': initiative_result['failure_count'],
            'failure_count': failures,
            'epic_count': len(epic_results),
            'story_count': len(story_results) + len(direct_story_results),
            'nested_story_count': len(story_results),
            'direct_story_count': len(direct_story_results),
            'all_items_count': len(all_items),
            'initiative_story_points': _point_value(initiative_own_story_points),
            'epic_story_points': _point_value(epic_own_story_points),
            'story_story_points': _point_value(story_own_story_points),
            'nested_story_points': _point_value(nested_story_own_story_points),
            'direct_story_points': _point_value(direct_story_own_story_points),
            'story_points_total': _point_value(rolled_story_points),
            'rolled_story_points': _point_value(rolled_story_points),
            'excluded_criteria': sorted(self.excluded_criteria),
        }
        canonical = json.dumps(result, sort_keys=True, separators=(',', ':'))
        result['snapshot_hash'] = hashlib.sha256(canonical.encode()).hexdigest()
        return result
