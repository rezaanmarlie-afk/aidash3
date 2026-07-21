import copy
import csv
import hmac
import io
import json
import re
import secrets
from datetime import datetime
from time import monotonic
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .compliance import ComplianceEngine, DEFAULT_FIELD_NAMES, rich_text_to_text
from .db import Database
from .jira_client import JiraClient, JiraError
from .pdf_export import build_detail_pdf, build_summary_pdf
from .settings import settings

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
APP_VERSION = (ROOT_DIR / 'VERSION').read_text(encoding='utf-8').strip() if (ROOT_DIR / 'VERSION').exists() else 'unknown'
app = FastAPI(title=f'{settings.app_name} v{APP_VERSION}')
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, same_site='lax', https_only=False)
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=BASE_DIR / 'templates')
db = Database(settings.data_path / 'pi_readiness.db')
jira = JiraClient(settings, BASE_DIR / 'mock_data.json')
_SCAN_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

CRITERIA_OPTIONS = [
    {'key': 'dor', 'label': 'Definition of Ready (DoR / DOR)'},
    {'key': 'dod', 'label': 'Definition of Done (DoD / DOD)'},
    {'key': 'acceptance_criteria', 'label': 'Acceptance Criteria'},
    {'key': 'dependencies', 'label': 'Known Dependencies'},
    {'key': 'story_estimation', 'label': 'Story Estimation / Sizing'},
    {'key': 'has_epics', 'label': 'Top-level Ticket Has Linked Epics'},
    {'key': 'epics_have_stories', 'label': 'Epics Have Stories'},
]
CUSTOM_RULE_OPTIONS = [
    {'key': 'required', 'label': 'Required / must be populated'},
    {'key': 'equals', 'label': 'Must equal'},
    {'key': 'not_equals', 'label': 'Must not equal'},
    {'key': 'contains', 'label': 'Must contain'},
    {'key': 'one_of', 'label': 'Must be one of'},
    {'key': 'numeric_min', 'label': 'Minimum numeric value'},
    {'key': 'numeric_max', 'label': 'Maximum numeric value'},
    {'key': 'boolean_true', 'label': 'Must be Yes / True / Complete'},
]
VALID_CUSTOM_RULES = {item['key'] for item in CUSTOM_RULE_OPTIONS}
VALID_APPLIES_TO = {'all', 'top_level', 'epic', 'story'}

DEFAULT_INITIATIVE_SIZE_THRESHOLDS = [
    {'code': 'XS', 'label': 'Extra Small', 'max_points': 20},
    {'code': 'S', 'label': 'Small', 'max_points': 50},
    {'code': 'M', 'label': 'Medium', 'max_points': 100},
    {'code': 'L', 'label': 'Large', 'max_points': 200},
    {'code': 'XL', 'label': 'Extra Large', 'max_points': 400},
]
OVERFLOW_INITIATIVE_SIZE = {'code': 'XXL', 'label': 'Extra Extra Large', 'max_points': None}


def initiative_size_thresholds() -> list[dict[str, Any]]:
    raw = db.get_setting('initiative_size_thresholds', DEFAULT_INITIATIVE_SIZE_THRESHOLDS)
    if not isinstance(raw, list):
        raw = DEFAULT_INITIATIVE_SIZE_THRESHOLDS
    thresholds: list[dict[str, Any]] = []
    fallback = {item['code']: item for item in DEFAULT_INITIATIVE_SIZE_THRESHOLDS}
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip().upper()
        if code not in fallback:
            continue
        try:
            max_points = float(item.get('max_points'))
        except (TypeError, ValueError):
            max_points = float(fallback[code]['max_points'])
        if max_points <= 0:
            max_points = float(fallback[code]['max_points'])
        thresholds.append({
            'code': code,
            'label': str(item.get('label') or fallback[code]['label']),
            'max_points': _point_value(max_points),
        })
    if not thresholds:
        thresholds = [dict(item) for item in DEFAULT_INITIATIVE_SIZE_THRESHOLDS]
    # Ensure the bands are ordered by the numeric max even when a setting was
    # edited manually in the database. Later settings save enforces monotonicity.
    return sorted(thresholds, key=lambda item: float(item.get('max_points') or 0))


def classify_initiative_size(points: float | int | str | None, thresholds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    total = float(points or 0)
    if total <= 0:
        return {
            'code': 'Unestimated',
            'label': 'Unestimated',
            'max_points': 0,
            'basis': 'No rolled-up story points found',
        }
    active_thresholds = thresholds or initiative_size_thresholds()
    previous_max = 0.0
    for band in active_thresholds:
        max_points = float(band.get('max_points') or 0)
        if total <= max_points:
            min_display = _point_value(previous_max + 0.01) if previous_max else 1
            return {
                'code': band['code'],
                'label': band.get('label') or band['code'],
                'max_points': _point_value(max_points),
                'basis': f'{_point_value(total)} rolled-up SP falls in {min_display}-{_point_value(max_points)} SP',
            }
        previous_max = max_points
    return {
        'code': OVERFLOW_INITIATIVE_SIZE['code'],
        'label': OVERFLOW_INITIATIVE_SIZE['label'],
        'max_points': None,
        'basis': f'{_point_value(total)} rolled-up SP is above {_point_value(previous_max)} SP',
    }


def apply_initiative_size(result: dict[str, Any], thresholds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    size = classify_initiative_size(result.get('story_points_total', 0), thresholds)
    result['initiative_size'] = size
    result['initiative_size_code'] = size['code']
    result['initiative_size_label'] = size['label']
    result['initiative_size_basis'] = size['basis']
    if 'initiative' in result and isinstance(result['initiative'], dict):
        result['initiative']['initiative_size'] = size
        result['initiative']['initiative_size_code'] = size['code']
    return result


def stored_additional_criteria() -> list[dict[str, Any]]:
    raw = db.get_setting('additional_criteria', [])
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get('field_id') or '').strip():
            continue
        criterion_id = str(item.get('id') or '').strip() or secrets.token_hex(6)
        rule = str(item.get('rule') or 'required').strip().lower()
        applies_to = str(item.get('applies_to') or 'all').strip().lower()
        result.append({
            'id': criterion_id,
            'key': f'custom:{criterion_id}',
            'field_id': str(item.get('field_id')).strip(),
            'field_name': str(item.get('field_name') or item.get('field_id')).strip(),
            'label': str(item.get('label') or item.get('field_name') or item.get('field_id')).strip(),
            'rule': rule if rule in VALID_CUSTOM_RULES else 'required',
            'expected': str(item.get('expected') or '').strip(),
            'applies_to': applies_to if applies_to in VALID_APPLIES_TO else 'all',
        })
    return result


def all_criteria_options() -> list[dict[str, str]]:
    options = list(CRITERIA_OPTIONS)
    options.extend(
        {'key': item['key'], 'label': f"Additional: {item['label']}"}
        for item in stored_additional_criteria()
    )
    return options


def criteria_label_map() -> dict[str, str]:
    return {item['key']: item['label'] for item in all_criteria_options()}


def _norm_field_label(value: str) -> str:
    return ''.join(ch for ch in str(value or '').casefold() if ch.isalnum())


def story_point_field_candidates(fields: list[dict[str, Any]], mapping: dict[str, str]) -> list[str]:
    """Discover all likely Story Points custom fields exposed by Jira.

    Jira estates frequently have duplicate Story Points fields, especially where
    company-managed and team-managed projects coexist. The saved mapping is
    kept first, but the scan also requests likely alternatives so the roll-up
    can use the field that is actually populated on NMGOS tickets.
    """
    candidates: list[str] = []
    mapped = mapping.get('story_points')
    if mapped:
        candidates.append(mapped)

    strong_exact = {
        'storypoints', 'storypoint', 'storypointestimate', 'storypointsestimate',
        'storyestimate', 'estimationpoints', 'sizepoints', 'sizingpoints',
    }
    for field in fields:
        field_id = str(field.get('id') or '').strip()
        if not field_id:
            continue
        labels = [str(field.get('name') or '')]
        labels.extend(str(clause or '') for clause in field.get('clauseNames') or [])
        normalised = {_norm_field_label(label) for label in labels if label}
        text = ' '.join(labels).casefold()
        if (
            normalised & strong_exact
            or ('story' in text and ('point' in text or 'estimate' in text or 'estim' in text or 'sizing' in text or 'size' in text))
        ):
            candidates.append(field_id)

    return list(dict.fromkeys(candidates))


def business_impact_field_candidates(fields: list[dict[str, Any]], mapping: dict[str, str]) -> list[str]:
    """Discover Business Impact fields exposed by Jira for the NMGOS top level.

    v1.29 intentionally stops using arbitrary custom fields that merely contain
    the word dependency. Known Dependencies may be satisfied by Business Impact
    only on the top-level NMGOS ticket, so request the configured mapping first
    and then name-based Business Impact candidates from the project/global field
    catalogue.
    """
    candidates: list[str] = []
    mapped = mapping.get('business_impact')
    if mapped:
        candidates.append(mapped)

    strong_exact = {
        'businessimpact', 'businessimpacts', 'businessimpactvalue',
        'businessimpactvaluebenefit', 'businessimpactandvalue',
        'businessimpactasoc', 'asocbusinessimpact',
    }
    for field in fields:
        field_id = str(field.get('id') or '').strip()
        if not field_id:
            continue
        labels = [str(field.get('name') or '')]
        labels.extend(str(clause or '') for clause in field.get('clauseNames') or [])
        normalised = {_norm_field_label(label) for label in labels if label}
        text = ' '.join(labels).casefold()
        if normalised & strong_exact or ('business impact' in text):
            candidates.append(field_id)

    return list(dict.fromkeys(candidates))


@app.middleware('http')
async def expose_build_and_disable_stale_browser_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-ASOC-App-Version'] = APP_VERSION
    # The app is frequently replaced in-place. Prevent browsers and proxies from
    # continuing to display old templates, JavaScript or CSS after an upgrade.
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.on_event('startup')
async def announce_running_build():
    print(f'ASOC PI Readiness build v{APP_VERSION}')
    print(f'Application source: {ROOT_DIR}')
    print(f'Data directory: {settings.data_path.resolve()}')


def clear_scan_cache():
    _SCAN_CACHE.clear()


def scan_cache_key(filters: dict[str, Any]) -> str:
    return json.dumps({k: filters.get(k, '') for k in sorted(filters)}, sort_keys=True)


def _valid_exclusions(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    valid_keys = {item['key'] for item in all_criteria_options()}
    return sorted({str(value).strip() for value in values if str(value).strip() in valid_keys})


def current_filters(request: Request) -> dict[str, Any]:
    return {
        'project': request.query_params.get('project') or settings.jira_project,
        'pi_value': request.query_params.get('pi_value') or settings.default_pi_value,
        'scrum_master_id': request.query_params.get('scrum_master_id') or settings.default_scrum_master_id,
        'scrum_master_name': request.query_params.get('scrum_master_name') or settings.default_scrum_master_name,
        'priority': request.query_params.get('priority') or settings.default_priority,
        'excluded_criteria': _valid_exclusions(request.query_params.getlist('exclude')),
    }


def filters_query(filters: dict[str, Any], **extra: Any) -> str:
    params: list[tuple[str, str]] = []
    for key in ('project', 'pi_value', 'priority', 'scrum_master_id', 'scrum_master_name'):
        params.append((key, str(filters.get(key, ''))))
    for criterion in filters.get('excluded_criteria') or []:
        params.append(('exclude', str(criterion)))
    for key, value in extra.items():
        params.append((key, str(value)))
    return urlencode(params, doseq=True)


def require_login(request: Request):
    if not request.session.get('authenticated'):
        raise HTTPException(status_code=401)


def csrf_token(request: Request) -> str:
    token = request.session.get('csrf')
    if not token:
        token = secrets.token_urlsafe(24)
        request.session['csrf'] = token
    return token


def verify_csrf(request: Request, token: str):
    expected = request.session.get('csrf', '')
    if not expected or not hmac.compare_digest(expected, token or ''):
        raise HTTPException(status_code=400, detail='Invalid form token')


def field_config() -> dict[str, Any]:
    fields = jira.fields()
    by_id = {f.get('id'): f for f in fields}
    mapping: dict[str, str] = {}
    for logical, names in DEFAULT_FIELD_NAMES.items():
        saved = db.get_setting(f'field.{logical}')
        if saved and saved in by_id:
            mapping[logical] = saved
            continue
        resolved = jira.resolve_field(names)
        if resolved:
            mapping[logical] = resolved['id']

    additional_criteria = []
    for criterion in stored_additional_criteria():
        field = by_id.get(criterion['field_id']) or {}
        configured = dict(criterion)
        configured['field_name'] = str(field.get('name') or criterion.get('field_name') or criterion['field_id'])
        configured['label'] = str(criterion.get('label') or configured['field_name'])
        additional_criteria.append(configured)

    pi_preferred_clause = db.get_setting('jql.pi_clause', 'PI Priority (ASOC)')
    sm_preferred_clause = db.get_setting(
        'jql.scrum_master_clause', 'Scrum Master[User Picker (single user)]'
    )

    pi_saved = db.get_setting('field.pi_priority')
    pi_field = by_id.get(pi_saved) if pi_saved else jira.resolve_field([pi_preferred_clause, 'PI Priority (ASOC)'])

    sm_saved = db.get_setting('field.scrum_master')
    sm_saved_field = by_id.get(sm_saved) if sm_saved else None
    sm_auto_field = jira.resolve_field([sm_preferred_clause, 'Scrum Master[User Picker (single user)]', 'Scrum Master'])

    # Older versions could persist Scrum Master[Dropdown] because both fields
    # share the same display name. Prefer the exact user-picker field when Jira
    # exposes it, so an existing local database self-corrects after upgrade.
    saved_clauses = {
        str(c).strip().lower()
        for c in (sm_saved_field or {}).get('clauseNames', []) or []
    }
    sm_field = (
        sm_saved_field
        if sm_saved_field and str(sm_preferred_clause).strip().lower() in saved_clauses
        else (sm_auto_field or sm_saved_field)
    )

    def clause(field: dict | None, preferred: str) -> str:
        if not field:
            return preferred
        clauses = field.get('clauseNames') or []
        exact = next((c for c in clauses if c.lower() == preferred.lower()), None)
        return exact or (clauses[0] if clauses else field.get('name', preferred))

    # NMGOS uses the exact Jira custom field `Target end (customfield_10023)`.
    # Prefer the saved mapping, otherwise bind to customfield_10023 when Jira
    # exposes it. This avoids relying on display-name/schema guessing.
    if not mapping.get('target_end'):
        if 'customfield_10023' in by_id:
            mapping['target_end'] = 'customfield_10023'
        else:
            resolved_target_end = jira.resolve_field(DEFAULT_FIELD_NAMES.get('target_end', ['Target end']))
            if resolved_target_end:
                mapping['target_end'] = resolved_target_end['id']

    parent_link_field = jira.resolve_field(['Parent Link'])
    epic_link_field = jira.resolve_field(['Epic Link'])
    story_point_ids = story_point_field_candidates(fields, mapping)
    business_impact_ids = business_impact_field_candidates(fields, mapping)

    return {
        'mapping': mapping,
        'story_point_field_ids': story_point_ids,
        'story_point_field_names': [
            f"{(by_id.get(field_id) or {}).get('name', field_id)} ({field_id})"
            for field_id in story_point_ids
        ],
        'business_impact_field_ids': business_impact_ids,
        'business_impact_field_names': [
            f"{(by_id.get(field_id) or {}).get('name', field_id)} ({field_id})"
            for field_id in business_impact_ids
        ],
        'pi_field': pi_field,
        'sm_field': sm_field,
        'parent_link_field': parent_link_field,
        'epic_link_field': epic_link_field,
        # JQL clauses are stored separately from field IDs. This prevents Jira
        # duplicate-name fields from silently changing the query semantics.
        'pi_clause': pi_preferred_clause or clause(pi_field, 'PI Priority (ASOC)'),
        'sm_clause': sm_preferred_clause or clause(sm_field, 'Scrum Master[User Picker (single user)]'),
        'fields': fields,
        'internal_projects': db.get_setting('internal_projects', [settings.jira_project]),
        'allow_description_fallback': db.get_setting('allow_description_fallback', True),
        'initiative_issue_type': db.get_setting('initiative_issue_type', 'Initiative'),
        'restrict_initiative_type': db.get_setting('restrict_initiative_type', False),
        'additional_criteria': additional_criteria,
        'initiative_size_thresholds': initiative_size_thresholds(),
    }


def select_top_level_issues(
    base_matches: list[dict], configured_issue_type: str, restrict_issue_type: bool
) -> list[dict]:
    """Select the top-level tickets returned by the manager's scope JQL.

    By default, the JQL result is authoritative. Jira issue-type names are
    organisation-specific, so filtering is only applied when explicitly
    enabled in Settings.
    """
    if not restrict_issue_type:
        return list(base_matches)
    target = str(configured_issue_type or 'Initiative').strip().casefold()
    return [
        issue for issue in base_matches
        if JiraClient.issue_type_name(issue).strip().casefold() == target
    ]



def _point_value(value: float | int | str | None) -> int | float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else round(number, 2)


def apply_descendant_story_point_rollup(
    result: dict[str, Any], descendants: list[dict], engine: ComplianceEngine
) -> dict[str, Any]:
    """Add story points from all descendant delivery work not in the normal tree.

    The normal compliance tree remains top-level→Epic→Story. This roll-up is
    intentionally broader because Jira projects can place pointed delivery work
    under Features, Tasks, Capabilities or extra hierarchy levels. These extra
    descendants are informational and do not change compliance scoring.
    """
    counted_keys = {result.get('initiative', {}).get('key')}
    for epic in result.get('epics', []) or []:
        counted_keys.add(epic.get('key'))
        for story in epic.get('stories', []) or []:
            counted_keys.add(story.get('key'))
    for story in result.get('direct_stories', []) or []:
        counted_keys.add(story.get('key'))

    additional: list[dict[str, Any]] = []
    additional_points = 0.0
    for issue in descendants or []:
        key = issue.get('key')
        if not key or key in counted_keys:
            continue
        evaluated = engine.evaluate_issue(issue, 'descendant')
        points = float(evaluated.get('story_points') or 0)
        if points <= 0:
            # Keep unpointed descendants out of the details to avoid clutter,
            # but they remain counted in diagnostics as loaded descendants.
            continue
        evaluated['hierarchy_role'] = 'Additional descendant work'
        additional.append(evaluated)
        additional_points += points
        counted_keys.add(key)

    if additional:
        current_total = float(result.get('story_points_total') or 0)
        new_total = current_total + additional_points
        result['additional_descendants'] = additional
        result['additional_descendant_count'] = len(additional)
        result['additional_descendant_story_points'] = _point_value(additional_points)
        result['story_points_total'] = _point_value(new_total)
        result['rolled_story_points'] = _point_value(new_total)
        result['other_descendant_story_points'] = _point_value(additional_points)
        result['initiative']['story_points_from_other_descendants'] = _point_value(additional_points)
        result['initiative']['rolled_story_points'] = _point_value(new_total)
        result['initiative']['total_story_points'] = _point_value(new_total)
    else:
        result.setdefault('additional_descendants', [])
        result.setdefault('additional_descendant_count', 0)
        result.setdefault('additional_descendant_story_points', 0)
        result.setdefault('other_descendant_story_points', 0)
    return result



def _dependency_check_failed(evaluated_issue: dict[str, Any]) -> bool:
    """Return True when an evaluated issue failed the Known Dependencies check."""
    for check in evaluated_issue.get('checks', []) or []:
        if check.get('key') == 'dependencies' and check.get('applicable', True):
            return not bool(check.get('passed'))
    return False


def _dependency_failed_keys(result: dict[str, Any]) -> list[str]:
    """Collect issue keys whose Known Dependencies check failed in a tree result."""
    keys: list[str] = []

    def add(issue: dict[str, Any] | None):
        if issue and _dependency_check_failed(issue):
            key = str(issue.get('key') or '').strip()
            if key and key not in keys:
                keys.append(key)

    add(result.get('initiative'))
    for epic in result.get('epics', []) or []:
        add(epic)
        for story in epic.get('stories', []) or []:
            add(story)
    for story in result.get('direct_stories', []) or []:
        add(story)
    return keys


def _merge_full_issue_fields(target_issue: dict[str, Any], full_issue: dict[str, Any]) -> bool:
    """Merge a full Jira issue payload into an already-loaded scan issue.

    The scan uses a small field list for performance. For the manager's broad
    Known Dependencies rule, a failing ticket needs a one-off all-fields lookup.
    This function merges the all-fields response without discarding enrichment
    metadata already attached to the scan issue, such as story-point candidates.
    """
    full_fields = full_issue.get('fields') or {}
    if not isinstance(full_fields, dict) or not full_fields:
        return False
    target_fields = target_issue.setdefault('fields', {})
    if not isinstance(target_fields, dict):
        target_issue['fields'] = {}
        target_fields = target_issue['fields']
    target_fields.update(full_fields)
    target_issue['_dependency_full_fields_loaded'] = True
    return True


def _apply_dependency_business_impact_only_note() -> str:
    """v1.26: no per-ticket full-field fallback is performed.

    The dependency fallback now checks Business Impact fields that are already
    mapped, globally discovered, or metadata-enriched during the normal bulk
    scan. This keeps scans fast and avoids one extra Jira fields=*all call for
    every failed dependency check.
    """
    return 'disabled; Business Impact fields only'

def run_scan(filters: dict[str, Any], force_refresh: bool = False) -> dict[str, Any]:
    cache_key = scan_cache_key(filters)
    cached = _SCAN_CACHE.get(cache_key)
    if not force_refresh and cached and monotonic() - cached[0] <= settings.scan_cache_seconds:
        result = copy.deepcopy(cached[1])
        result.setdefault('diagnostics', {})['cache_hit'] = True
        return result

    jira.reset_metrics()
    started = monotonic()
    cfg = field_config()
    missing_clauses = [
        name for name, value in [
            ('PI Priority JQL field clause', cfg['pi_clause']),
            ('Scrum Master JQL field clause', cfg['sm_clause']),
        ] if not str(value or '').strip()
    ]
    if missing_clauses:
        raise JiraError('JQL configuration is incomplete: ' + ', '.join(missing_clauses) + '.')

    jql = jira.build_jql(
        filters['project'], cfg['pi_clause'], filters['pi_value'], filters['priority'],
        cfg['sm_clause'], filters['scrum_master_id']
    )
    if cfg['restrict_initiative_type']:
        issue_type = cfg['initiative_issue_type'].replace('"', '\\"')
        jql = jql.replace(' ORDER BY', f' AND issuetype = "{issue_type}" ORDER BY')

    relation_ids = [
        (cfg.get('parent_link_field') or {}).get('id'),
        (cfg.get('epic_link_field') or {}).get('id'),
    ]
    required_fields = list(dict.fromkeys([
        'summary', 'issuetype', 'status', 'assignee', 'description',
        'issuelinks', 'timetracking', 'parent', 'duedate', 'resolutiondate', 'statuscategorychangedate',
        *cfg['mapping'].values(),
        *cfg.get('story_point_field_ids', []),
        *cfg.get('business_impact_field_ids', []),
        *[criterion['field_id'] for criterion in cfg.get('additional_criteria', []) if criterion.get('field_id')],
        *[field_id for field_id in relation_ids if field_id],
    ]))

    base_matches = jira.search(
        jql, fields=required_fields, max_results=settings.jira_scan_max_results
    )

    # The user's scope JQL already identifies the prioritised top-level work.
    # Do not silently discard valid results just because a Jira site calls the
    # issue type "Initiate", "Signature Project", "Feature", or another
    # organisation-specific name. Only apply an issue-type filter when the
    # manager explicitly enables it in Settings. This restores the behaviour
    # of v1.1 while retaining the v1.2 bulk hierarchy performance improvement.
    initiatives = select_top_level_issues(
        base_matches, cfg['initiative_issue_type'], cfg['restrict_initiative_type']
    )

    parent_link_id = (cfg.get('parent_link_field') or {}).get('id')
    epic_link_id = (cfg.get('epic_link_field') or {}).get('id')
    epics_by_initiative, stories_by_epic, hierarchy_stats = jira.bulk_hierarchy(
        initiatives,
        fields=required_fields,
        parent_link_field_id=parent_link_id,
        epic_link_field_id=epic_link_id,
        max_results=settings.jira_scan_max_results,
    )
    direct_stories_by_initiative = hierarchy_stats.get('_direct_stories_by_initiative', {}) or {}

    descendants_by_initiative, descendant_stats = jira.bulk_descendant_issues(
        initiatives,
        fields=required_fields,
        parent_link_field_id=parent_link_id,
        epic_link_field_id=epic_link_id,
        max_results=settings.jira_scan_max_results,
        max_depth=6,
    )

    # Story Points can differ by Jira board/workspace. The initial bulk scan
    # requests configured/global candidates for performance; this enrichment
    # performs a metadata-aware second pass across the loaded hierarchy so
    # team-managed or board-specific estimation fields are not missed.
    all_loaded_issues = list({
        issue.get('key'): issue
        for issue in [
            *initiatives,
            *[epic for epics in epics_by_initiative.values() for epic in epics],
            *[story for stories in stories_by_epic.values() for story in stories],
            *[story for stories in direct_stories_by_initiative.values() for story in stories],
            *[issue for issues in descendants_by_initiative.values() for issue in issues],
        ]
        if issue.get('key')
    }.values())
    story_point_enrichment = jira.enrich_story_points_from_issue_metadata(
        all_loaded_issues,
        known_field_ids=cfg.get('story_point_field_ids', []),
        max_results=settings.jira_scan_max_results,
    )

    engine_mapping = dict(cfg['mapping'])
    engine_mapping['story_points_candidates'] = cfg.get('story_point_field_ids', [])
    engine_mapping['business_impact_candidates'] = cfg.get('business_impact_field_ids', [])
    engine = ComplianceEngine(
        engine_mapping, cfg['internal_projects'], cfg['allow_description_fallback'],
        excluded_criteria=set(filters.get('excluded_criteria') or []),
        additional_criteria=cfg.get('additional_criteria', []),
    )
    size_thresholds = cfg.get('initiative_size_thresholds') or initiative_size_thresholds()
    results = []
    for initiative in initiatives:
        epics = epics_by_initiative.get(initiative['key'], [])
        scoped_stories = {
            epic['key']: stories_by_epic.get(epic['key'], [])
            for epic in epics
        }
        direct_stories = direct_stories_by_initiative.get(initiative['key'], [])

        issue_by_key: dict[str, dict[str, Any]] = {}

        def register_issue(issue: dict[str, Any] | None):
            if issue and issue.get('key'):
                issue_by_key[str(issue['key'])] = issue

        register_issue(initiative)

        # Persist the exact configured Target end field on the Initiative so PI
        # Yield always reads the same Jira field selected in Settings. NMGOS
        # currently uses customfield_10023.
        target_end_field_id = str(cfg.get('mapping', {}).get('target_end') or 'customfield_10023')
        initiative_fields = initiative.get('fields') or {}
        initiative['target_end_date'] = initiative_fields.get(target_end_field_id)
        initiative['_target_end_field_id'] = target_end_field_id
        field_catalog_by_id = {str(item.get('id')): item for item in cfg.get('fields', []) if item.get('id')}
        initiative['_target_end_field_name'] = str(
            (field_catalog_by_id.get(target_end_field_id) or {}).get('name') or 'Target end'
        )

        for epic in epics:
            register_issue(epic)
            for story in scoped_stories.get(epic.get('key'), []) or []:
                register_issue(story)
        for story in direct_stories:
            register_issue(story)

        result = engine.evaluate_tree(initiative, epics, scoped_stories, direct_stories=direct_stories)

        # The compliance engine intentionally returns a compact presentation
        # object and does not retain Jira's raw fields. PI Yield, however, must
        # evaluate the Initiative's exact Target end and resolutiondate values.
        # Preserve only the small set of raw Initiative fields needed by the
        # Yield calculation instead of carrying the full Jira payload.
        compact_initiative = result.get('initiative') or {}
        compact_initiative['fields'] = {
            target_end_field_id: initiative_fields.get(target_end_field_id),
            'customfield_10023': initiative_fields.get('customfield_10023'),
            'duedate': initiative_fields.get('duedate'),
            'resolutiondate': initiative_fields.get('resolutiondate'),
            'statuscategorychangedate': initiative_fields.get('statuscategorychangedate'),
        }
        compact_initiative['target_end_date'] = initiative.get('target_end_date')
        compact_initiative['_target_end_field_id'] = target_end_field_id
        compact_initiative['_target_end_field_name'] = initiative.get('_target_end_field_name') or 'Target end'

        result = apply_descendant_story_point_rollup(
            result,
            descendants_by_initiative.get(initiative['key'], []),
            engine,
        )
        result = apply_initiative_size(result, size_thresholds)
        latest = db.latest_signoff(initiative['key'], filters['pi_value'], filters['scrum_master_id'])
        if latest:
            latest['is_current'] = latest['snapshot_hash'] == result['snapshot_hash']
        result['latest_signoff'] = latest
        results.append(result)

    db.log_run(jql, filters['pi_value'], filters['scrum_master_id'], len(results))
    compliant = sum(1 for result in results if result['compliant'])
    approved_current = sum(
        1 for result in results
        if result.get('latest_signoff')
        and result['latest_signoff']['decision'] == 'APPROVED'
        and result['latest_signoff']['is_current']
    )
    scan = {
        'jql': jql,
        'results': results,
        'summary': {
            'initiatives': len(results),
            'compliant': compliant,
            'blocked': len(results) - compliant,
            'approved': approved_current,
            'ticket_score': round(
                sum(result['ticket_score'] for result in results) / len(results), 1
            ) if results else 0,
            'hierarchy_score': round(
                sum(result['hierarchy_score'] for result in results) / len(results), 1
            ) if results else 0,
            'score': round(
                sum(result['hierarchy_score'] for result in results) / len(results), 1
            ) if results else 0,
            'story_points_total': sum(float(result.get('story_points_total') or 0) for result in results),
            'initiative_story_points': sum(float(result.get('initiative_story_points') or 0) for result in results),
            'epic_story_points': sum(float(result.get('epic_story_points') or 0) for result in results),
            'story_story_points': sum(float(result.get('story_story_points') or 0) for result in results),
            'direct_story_points': sum(float(result.get('direct_story_points') or 0) for result in results),
            'nested_story_points': sum(float(result.get('nested_story_points') or 0) for result in results),
            'additional_descendant_story_points': sum(float(result.get('additional_descendant_story_points') or 0) for result in results),
            'additional_descendant_count': sum(int(result.get('additional_descendant_count') or 0) for result in results),
            'initiative_size_distribution': {
                code: sum(1 for result in results if result.get('initiative_size_code') == code)
                for code in ['Unestimated', *[band['code'] for band in size_thresholds], OVERFLOW_INITIATIVE_SIZE['code']]
            },
        },
        'diagnostics': {
            'elapsed_seconds': round(monotonic() - started, 2),
            'jira_requests': jira.request_count,
            'base_matches': len(base_matches),
            'non_initiative_matches_skipped': len(base_matches) - len(initiatives),
            'issue_type_filter_enabled': bool(cfg['restrict_initiative_type']),
            'issue_types_returned': sorted({
                jira.issue_type_name(issue) or '(unknown)' for issue in base_matches
            }),
            'epics_loaded': hierarchy_stats['epics_loaded'],
            'stories_loaded': hierarchy_stats['stories_loaded'],
            'nested_stories_loaded': hierarchy_stats.get('nested_stories_loaded', hierarchy_stats.get('stories_loaded', 0)),
            'direct_stories_loaded': hierarchy_stats.get('direct_stories_loaded', 0),
            'descendant_issues_loaded': descendant_stats.get('descendant_issues_loaded', 0),
            'descendant_rollup_depth': descendant_stats.get('descendant_rollup_depth', 0),
            'descendant_linked_issues_loaded': descendant_stats.get('descendant_linked_issues_loaded', 0),
            'story_point_fields_requested': cfg.get('story_point_field_names', []),
            'business_impact_fields_requested': cfg.get('business_impact_field_names', []),
            'business_impact_dynamic_fields': story_point_enrichment.get('dynamic_business_impact_field_names', []),
            'business_impact_issues_metadata_enriched': story_point_enrichment.get('business_impact_issues_enriched', 0),
            'dependency_fallback_scope': 'Business Impact fields only',
            'dependency_full_field_fallback_count': 0,
            'story_point_dynamic_fields': story_point_enrichment.get('dynamic_field_names', []),
            'story_point_issues_metadata_enriched': story_point_enrichment.get('issues_enriched', 0),
            'story_point_enrichment_warning': story_point_enrichment.get('warning', ''),
            'cache_hit': False,
        },
        'config': cfg,
        'excluded_criteria': [
            {'key': key, 'label': criteria_label_map().get(key, key)}
            for key in filters.get('excluded_criteria') or []
        ],
    }
    _SCAN_CACHE[cache_key] = (monotonic(), copy.deepcopy(scan))
    while len(_SCAN_CACHE) > 10:
        _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)))
    return scan


def render(request: Request, template: str, context: dict[str, Any], status_code: int = 200):
    base = {
        'request': request,
        'app_name': settings.app_name,
        'app_version': APP_VERSION,
        'mock_mode': settings.mock_mode,
        'deadline': settings.manager_signoff_deadline,
        'manager_name': settings.app_manager_name,
        'csrf_token': csrf_token(request),
        'authenticated': request.session.get('authenticated', False),
        'criteria_options': all_criteria_options(),
        'custom_rule_options': CUSTOM_RULE_OPTIONS,
    }
    base.update(context)
    return templates.TemplateResponse(template, base, status_code=status_code)


@app.exception_handler(401)
async def login_required(request: Request, exc: HTTPException):
    return RedirectResponse('/login', status_code=303)


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'version': APP_VERSION,
        'mock_mode': settings.mock_mode,
        'data_dir': str(settings.data_path.resolve()),
        'application_source': str(ROOT_DIR),
        'main_file': str(Path(__file__).resolve()),
    }


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, 'login.html', {'error': None})


@app.post('/login', response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
    verify_csrf(request, csrf)
    valid_user = hmac.compare_digest(username, settings.app_admin_username)
    valid_pass = hmac.compare_digest(password, settings.app_admin_password)
    if not (valid_user and valid_pass):
        return render(request, 'login.html', {'error': 'Invalid username or password.'}, status_code=401)
    request.session['authenticated'] = True
    request.session['username'] = username
    return RedirectResponse('/executive', status_code=303)


@app.post('/logout')
def logout(request: Request, csrf: str = Form(...)):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse('/login', status_code=303)


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    require_login(request)
    filters = current_filters(request)
    scan = None
    error = None
    if request.query_params.get('run') == '1':
        try:
            scan = run_scan(filters, force_refresh=True)
        except Exception as exc:
            error = str(exc)
    return render(request, 'index.html', {
        'filters': filters, 'filter_query': filters_query(filters), 'scan': scan, 'error': error
    })


@app.get('/initiative/{initiative_key}', response_class=HTMLResponse)
def initiative_detail(request: Request, initiative_key: str):
    require_login(request)
    filters = current_filters(request)
    try:
        scan = run_scan(filters)
        result = next((r for r in scan['results'] if r['initiative']['key'] == initiative_key), None)
        if not result:
            raise HTTPException(status_code=404, detail='Initiative is not in the current filtered result set.')
        return render(request, 'initiative.html', {
            'filters': filters, 'filter_query': filters_query(filters),
            'scan': scan, 'result': result, 'error': None,
            'writeback_enabled': settings.enable_jira_writeback,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return render(request, 'initiative.html', {
            'filters': filters, 'filter_query': filters_query(filters),
            'scan': None, 'result': None, 'error': str(exc),
            'writeback_enabled': settings.enable_jira_writeback,
        }, status_code=500)


@app.post('/initiative/{initiative_key}/signoff')
def signoff(
    request: Request,
    initiative_key: str,
    project: str = Form(...),
    pi_value: str = Form(...),
    scrum_master_id: str = Form(...),
    scrum_master_name: str = Form(''),
    priority: str = Form(...),
    excluded_criteria: str = Form(''),
    decision: str = Form(...),
    comment: str = Form(''),
    jira_writeback: str | None = Form(None),
    csrf: str = Form(...),
):
    require_login(request)
    verify_csrf(request, csrf)
    filters = {
        'project': project, 'pi_value': pi_value, 'scrum_master_id': scrum_master_id,
        'scrum_master_name': scrum_master_name, 'priority': priority,
        'excluded_criteria': _valid_exclusions(excluded_criteria.split(',')),
    }
    scan = run_scan(filters, force_refresh=True)
    result = next((r for r in scan['results'] if r['initiative']['key'] == initiative_key), None)
    if not result:
        raise HTTPException(status_code=404, detail='Initiative not found in the selected scope.')
    decision = decision.upper()
    if decision not in {'APPROVED', 'RETURNED'}:
        raise HTTPException(status_code=400, detail='Invalid sign-off decision.')
    if decision == 'APPROVED' and not result['compliant']:
        raise HTTPException(status_code=400, detail='A non-compliant Initiative cannot be approved. Return it for remediation.')
    if decision == 'RETURNED' and not comment.strip():
        raise HTTPException(status_code=400, detail='A remediation comment is required when returning an Initiative.')

    writeback_requested = bool(jira_writeback) and settings.enable_jira_writeback
    record = {
        'initiative_key': initiative_key,
        'pi_value': pi_value,
        'scrum_master_id': scrum_master_id,
        'decision': decision,
        'signer_name': settings.app_manager_name or request.session.get('username', 'Manager'),
        'signer_email': settings.app_manager_email,
        'comment': comment.strip(),
        'snapshot_hash': result['snapshot_hash'],
        'snapshot_json': result,
        'jira_writeback': writeback_requested,
    }
    db.save_signoff(record)
    clear_scan_cache()

    if writeback_requested:
        label_decision = 'approved' if decision == 'APPROVED' else 'remediation'
        comment_text = (
            f'ASOC PI Manager Sign-Off — {decision}\n'
            f'PI: {pi_value}\nScrum Master: {scrum_master_name or scrum_master_id}\n'
            f'Top-level ticket compliance: {result["ticket_score"]}%\n'
            f'Full hierarchy compliance: {result["hierarchy_score"]}%\n'
            f'Snapshot: {result["snapshot_hash"][:12]}\n'
            f'Manager: {record["signer_name"]}\nComment: {comment.strip() or "None"}'
        )
        jira.add_comment(initiative_key, comment_text)
        jira.add_labels(initiative_key, [
            f'{settings.signoff_label_prefix}-{label_decision}',
            f'{settings.signoff_label_prefix}-{pi_value.lower()}',
        ])

    params = filters_query(filters, run='1', saved=decision.lower())
    return RedirectResponse(f'/initiative/{initiative_key}?{params}', status_code=303)


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    require_login(request)
    field_project = (
        request.query_params.get('field_project')
        or db.get_setting('field_discovery_project', '')
        or str(settings.jira_project).split(',')[0].strip()
    )
    try:
        cfg = field_config()
        project_field_info = jira.project_fields(field_project)
        cfg['project_fields'] = project_field_info.get('fields', [])
        cfg['project_field_ids'] = {field.get('id') for field in cfg['project_fields']}
        cfg['field_project'] = field_project
        cfg['project_field_source'] = project_field_info.get('source', '')
        cfg['project_field_warning'] = project_field_info.get('warning', '')
        error = None
    except Exception as exc:
        cfg = {
            'fields': [], 'project_fields': [], 'project_field_ids': set(), 'mapping': {},
            'additional_criteria': stored_additional_criteria(),
            'internal_projects': [settings.jira_project],
            'allow_description_fallback': True, 'initiative_issue_type': 'Initiative',
            'restrict_initiative_type': False, 'pi_field': None, 'sm_field': None,
            'pi_clause': 'PI Priority (ASOC)',
            'sm_clause': 'Scrum Master[User Picker (single user)]',
            'field_project': field_project, 'project_field_source': '', 'project_field_warning': '',
            'initiative_size_thresholds': initiative_size_thresholds(),
        }
        error = str(exc)
    return render(request, 'settings.html', {
        'cfg': cfg, 'error': error, 'saved': request.query_params.get('saved')
    })


@app.post('/settings')
def save_settings(
    request: Request,
    pi_priority: str = Form(...),
    scrum_master: str = Form(...),
    dor: str = Form(''),
    dod: str = Form(''),
    acceptance_criteria: str = Form(''),
    dependencies: str = Form(''),
    business_impact: str = Form(''),
    target_end: str = Form(''),
    story_points: str = Form(''),
    squad: str = Form(''),
    internal_projects: str = Form(''),
    initiative_issue_type: str = Form('Initiative'),
    pi_jql_clause: str = Form('PI Priority (ASOC)'),
    scrum_master_jql_clause: str = Form('Scrum Master[User Picker (single user)]'),
    field_project: str = Form(''),
    custom_id: list[str] = Form(default=[]),
    custom_field_id: list[str] = Form(default=[]),
    custom_label: list[str] = Form(default=[]),
    custom_rule: list[str] = Form(default=[]),
    custom_expected: list[str] = Form(default=[]),
    custom_applies_to: list[str] = Form(default=[]),
    size_code: list[str] = Form(default=[]),
    size_label: list[str] = Form(default=[]),
    size_max_points: list[str] = Form(default=[]),
    restrict_initiative_type: str | None = Form(None),
    allow_description_fallback: str | None = Form(None),
    csrf: str = Form(...),
):
    require_login(request)
    verify_csrf(request, csrf)
    for key, value in {
        'pi_priority': pi_priority, 'scrum_master': scrum_master, 'dor': dor, 'dod': dod,
        'acceptance_criteria': acceptance_criteria, 'dependencies': dependencies,
        'business_impact': business_impact, 'target_end': target_end, 'story_points': story_points, 'squad': squad,
    }.items():
        if value:
            db.set_setting(f'field.{key}', value)
    db.set_setting('internal_projects', [p.strip().upper() for p in internal_projects.split(',') if p.strip()])
    db.set_setting('initiative_issue_type', initiative_issue_type.strip() or 'Initiative')
    db.set_setting('jql.pi_clause', pi_jql_clause.strip() or 'PI Priority (ASOC)')
    db.set_setting(
        'jql.scrum_master_clause',
        scrum_master_jql_clause.strip() or 'Scrum Master[User Picker (single user)]',
    )
    db.set_setting('restrict_initiative_type', bool(restrict_initiative_type))
    db.set_setting('allow_description_fallback', bool(allow_description_fallback))
    db.set_setting('field_discovery_project', field_project.strip() or str(settings.jira_project).split(',')[0].strip())

    field_catalog = jira.field_catalog()
    additional: list[dict[str, str]] = []
    row_count = min(
        max(len(custom_field_id), len(custom_id), len(custom_label), len(custom_rule), len(custom_expected), len(custom_applies_to)),
        30,
    )
    for index in range(row_count):
        field_id = custom_field_id[index].strip() if index < len(custom_field_id) else ''
        if not field_id:
            continue
        metadata = field_catalog.get(field_id) or {}
        criterion_id = custom_id[index].strip() if index < len(custom_id) else ''
        rule = custom_rule[index].strip().lower() if index < len(custom_rule) else 'required'
        expected = custom_expected[index].strip() if index < len(custom_expected) else ''
        applies_to = custom_applies_to[index].strip().lower() if index < len(custom_applies_to) else 'all'
        field_name = str(metadata.get('name') or field_id)
        label = custom_label[index].strip() if index < len(custom_label) else ''
        additional.append({
            'id': criterion_id or secrets.token_hex(6),
            'field_id': field_id,
            'field_name': field_name,
            'label': label or field_name,
            'rule': rule if rule in VALID_CUSTOM_RULES else 'required',
            'expected': expected,
            'applies_to': applies_to if applies_to in VALID_APPLIES_TO else 'all',
        })
    db.set_setting('additional_criteria', additional)

    thresholds: list[dict[str, Any]] = []
    previous = 0.0
    fallback = {item['code']: item for item in DEFAULT_INITIATIVE_SIZE_THRESHOLDS}
    for index, default_band in enumerate(DEFAULT_INITIATIVE_SIZE_THRESHOLDS):
        code = size_code[index].strip().upper() if index < len(size_code) and size_code[index].strip() else default_band['code']
        if code not in fallback:
            code = default_band['code']
        label = size_label[index].strip() if index < len(size_label) and size_label[index].strip() else fallback[code]['label']
        raw_max = size_max_points[index].strip() if index < len(size_max_points) else ''
        try:
            max_points = float(raw_max)
        except (TypeError, ValueError):
            max_points = float(fallback[code]['max_points'])
        # Keep the bands strictly increasing so every point total maps to one
        # unambiguous initiative size.
        if max_points <= previous:
            max_points = previous + 1
        previous = max_points
        thresholds.append({'code': code, 'label': label, 'max_points': _point_value(max_points)})
    db.set_setting('initiative_size_thresholds', thresholds)

    jira.fields(refresh=True)
    clear_scan_cache()
    return RedirectResponse('/settings?saved=1', status_code=303)


@app.get('/api/jira/users')
def users(request: Request, q: str):
    require_login(request)
    if len(q.strip()) < 2:
        return JSONResponse([])
    return JSONResponse(jira.search_users(q.strip()))


@app.get('/api/jira/project-fields')
def project_fields_api(request: Request, project: str, refresh: bool = True):
    require_login(request)
    result = jira.project_fields(project, refresh=refresh)
    return JSONResponse(result)


def _csv_safe(value: Any) -> Any:
    """Prevent spreadsheet formula execution when Jira text is opened as CSV."""
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def _csv_response(rows: list[list[Any]], filename: str) -> StreamingResponse:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    for row in rows:
        writer.writerow([_csv_safe(value) for value in row])
    # UTF-8 BOM helps Excel preserve Jira text and names correctly.
    content = '\ufeff' + output.getvalue()
    return StreamingResponse(
        iter([content]), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


def _export_timestamp() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M')


def _summary_export_rows(scan: dict[str, Any], filters: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [[
        'PI', 'Scrum Master', 'Top-Level Ticket', 'Issue Type', 'Summary',
        'Top-Level Ticket Compliance %', 'Top-Level Ticket Compliant',
        'Full Hierarchy Compliance %', 'Full Hierarchy Ready',
        'Epic Count', 'Story Count', 'Direct Story Count', 'Top-Level Story Points', 'Epic Story Points',
        'Story Story Points', 'Direct Story Points', 'Other Descendant Story Points', 'Rolled-Up Story Points',
        'Initiative Size', 'Initiative Size Basis', 'Top-Level Failures', 'Hierarchy Failures',
        'Excluded Criteria', 'Latest Sign-Off', 'Sign-Off Current', 'JQL'
    ]]
    for result in scan['results']:
        signoff = result.get('latest_signoff') or {}
        rows.append([
            filters['pi_value'], filters['scrum_master_name'] or filters['scrum_master_id'],
            result['initiative']['key'], result['initiative']['issue_type'],
            result['initiative']['summary'], result['ticket_score'],
            'Yes' if result['ticket_compliant'] else 'No', result['hierarchy_score'],
            'Yes' if result['compliant'] else 'No', result['epic_count'], result['story_count'],
            result.get('direct_story_count', 0), result.get('initiative_story_points', 0), result.get('epic_story_points', 0),
            result.get('story_story_points', 0), result.get('direct_story_points', 0), result.get('additional_descendant_story_points', 0), result.get('story_points_total', 0),
            result.get('initiative_size_code', ''), result.get('initiative_size_basis', ''),
            result['ticket_failure_count'], result['failure_count'],
            ', '.join(criteria_label_map().get(key, key) for key in filters.get('excluded_criteria') or []) or 'None',
            signoff.get('decision', ''),
            'Yes' if signoff.get('is_current') else ('No' if signoff else ''), scan['jql'],
        ])
    return rows


def _detailed_export_rows(
    scan: dict[str, Any], filters: dict[str, Any], initiative_key: str | None = None
) -> list[list[Any]]:
    rows: list[list[Any]] = [[
        'PI', 'Scrum Master', 'Root Ticket', 'Root Summary',
        'Root Ticket Compliance %', 'Full Hierarchy Compliance %', 'Full Hierarchy Ready',
        'Hierarchy Level', 'Parent Ticket', 'Issue Key', 'Issue Type', 'Issue Summary',
        'Status', 'Assignee', 'Issue Story Points', 'Issue Rolled-Up Story Points',
        'Root Rolled-Up Story Points', 'Issue Compliance %', 'Issue Compliant',
        'Criterion', 'Applicable', 'Excluded', 'Criterion Result', 'Evidence', 'Remediation',
        'Latest Sign-Off', 'Sign-Off Current', 'JQL', 'Root Initiative Size', 'Root Initiative Size Basis'
    ]]

    selected = [
        result for result in scan['results']
        if not initiative_key or result['initiative']['key'] == initiative_key
    ]
    for result in selected:
        root = result['initiative']
        signoff = result.get('latest_signoff') or {}
        common = [
            filters['pi_value'], filters['scrum_master_name'] or filters['scrum_master_id'],
            root['key'], root['summary'], result['ticket_score'], result['hierarchy_score'],
            'Yes' if result['compliant'] else 'No',
        ]

        def add_issue(item: dict[str, Any], level: str, parent_key: str = '') -> None:
            for check in item['checks']:
                rows.append(common + [
                    level, parent_key, item['key'], item['issue_type'], item['summary'],
                    item['status'], item['assignee'], item.get('story_points', 0),
                    item.get('rolled_story_points', item.get('story_points', 0)),
                    result.get('story_points_total', 0), item['score'],
                    'Yes' if item['passed'] else 'No', check['label'],
                    'Yes' if check['applicable'] else 'No',
                    'Yes' if check.get('excluded') else 'No',
                    'Excluded' if check.get('excluded') else (
                        'N/A' if not check['applicable'] else ('Pass' if check['passed'] else 'Fail')
                    ),
                    check['evidence'], check['remediation'], signoff.get('decision', ''),
                    'Yes' if signoff.get('is_current') else ('No' if signoff else ''), scan['jql'],
                    result.get('initiative_size_code', ''), result.get('initiative_size_basis', ''),
                ])

        add_issue(root, 'Top-level ticket')
        for story in result.get('direct_stories', []) or []:
            add_issue(story, 'Direct Story', root['key'])
        for item in result.get('additional_descendants', []) or []:
            add_issue(item, 'Additional descendant work', root['key'])
        for epic in result['epics']:
            add_issue(epic, 'Epic', root['key'])
            for story in epic.get('stories', []):
                add_issue(story, 'Story', epic['key'])

        for check in result['structural_checks']:
            rows.append(common + [
                'Hierarchy control', root['key'], root['key'], root['issue_type'], root['summary'],
                root['status'], root['assignee'], root.get('story_points', 0),
                root.get('rolled_story_points', result.get('story_points_total', 0)),
                result.get('story_points_total', 0), result['hierarchy_score'],
                'Yes' if result['compliant'] else 'No', check['label'],
                'Yes' if check['applicable'] else 'No', 'Yes' if check.get('excluded') else 'No',
                'Excluded' if check.get('excluded') else ('Pass' if check['passed'] else 'Fail'),
                check['evidence'], check['remediation'],
                signoff.get('decision', ''),
                'Yes' if signoff.get('is_current') else ('No' if signoff else ''), scan['jql'],
                result.get('initiative_size_code', ''), result.get('initiative_size_basis', ''),
            ])
    return rows



def _art_scan(art: dict[str, Any], force_refresh: bool = False) -> dict[str, Any]:
    combined=[]; diagnostics=[]; seen=set()
    for sm in art.get('scrum_masters', []):
        filters={'project':art['project'],'pi_value':art['pi_value'],'priority':art['priority'],
                 'scrum_master_id':sm.get('id',''),'scrum_master_name':sm.get('name',''), 'excluded_criteria':[]}
        scan=run_scan(filters, force_refresh=force_refresh)
        diagnostics.append({'scrum_master':sm.get('name') or sm.get('id'),'diagnostics':scan.get('diagnostics',{})})
        for result in scan.get('results',[]):
            key=result.get('initiative',{}).get('key')
            if key and key not in seen:
                item=copy.deepcopy(result); item['art_scrum_master']=sm.get('name') or sm.get('id'); combined.append(item); seen.add(key)
    total_sp=sum(float(r.get('story_points_total') or 0) for r in combined)
    return {'results':combined,'summary':{'initiatives':len(combined),'story_points_total':total_sp,
        'ticket_score':round(sum(r.get('ticket_score',0) for r in combined)/len(combined),1) if combined else 0,
        'hierarchy_score':round(sum(r.get('hierarchy_score',0) for r in combined)/len(combined),1) if combined else 0,
        'compliant':sum(1 for r in combined if r.get('compliant'))},'diagnostics':diagnostics}

def _all_work_items(result):
    items=[]
    for issue in [result.get('initiative')]:
        if issue: items.append(issue)
    for epic in result.get('epics',[]) or []:
        items.append(epic); items.extend(epic.get('stories',[]) or [])
    items.extend(result.get('direct_stories',[]) or [])
    items.extend(result.get('additional_descendants',[]) or [])
    return items

def _status_metrics(result):
    items=_all_work_items(result)
    total=float(result.get('story_points_total') or 0); done=0.0; blocked=0
    done_names={'done','closed','resolved','complete','completed','released','accepted'}
    blocked_names={'blocked','impediment','on hold'}
    started=0
    for issue in items:
        status=str(issue.get('status') or '').strip().casefold()
        points=float(issue.get('story_points') or 0)
        if any(x in status for x in done_names): done += points
        elif status and status not in {'to do','open','new','backlog'}: started += 1
        if any(x in status for x in blocked_names): blocked += 1
    progress=round((done/total*100),1) if total else 0
    if total and progress>=99.9: health='Completed'
    elif blocked: health='Blocked'
    elif progress>0 or started: health='In Progress'
    else: health='Not Started'
    return {'total_sp':total,'done_sp':round(done,2),'progress':progress,'blocked':blocked,'health':health}



def _rag(value: float, green: float, amber: float) -> dict[str, str]:
    if value >= green:
        return {'code': 'green', 'label': 'Green'}
    if value >= amber:
        return {'code': 'amber', 'label': 'Amber'}
    return {'code': 'red', 'label': 'Red'}


def _executive_metrics(art: dict[str, Any], scan: dict[str, Any], baseline: dict[str, Any] | None, allowed_days: int) -> dict[str, Any]:
    performance = _pi_yield_metrics(art, scan, baseline, allowed_days=allowed_days)
    summary = scan.get('summary', {})
    rows = performance.get('rows', [])
    total = int(summary.get('initiatives', 0) or 0)
    compliant = int(summary.get('compliant', 0) or 0)
    approved = sum(1 for r in scan.get('results', []) if (r.get('latest_signoff') or {}).get('decision') == 'APPROVED' and (r.get('latest_signoff') or {}).get('is_current'))
    blocked = sum(int(_status_metrics(r).get('blocked', 0)) for r in scan.get('results', []))
    baseline_sp = 0.0
    if baseline:
        baseline_sp = float((baseline.get('snapshot') or {}).get('story_points_total') or 0)
        if not baseline_sp:
            baseline_sp = sum(float(t.get('story_points_total') or 0) for t in (baseline.get('snapshot') or {}).get('tickets', []))
    current_sp = float(summary.get('story_points_total', 0) or 0)
    scope_delta = round(current_sp - baseline_sp, 1) if baseline else 0.0
    scope_delta_pct = round((scope_delta / baseline_sp * 100), 1) if baseline_sp else 0.0
    ticket_score = float(summary.get('ticket_score', 0) or 0)
    hierarchy_score = float(summary.get('hierarchy_score', 0) or 0)
    yield_pct = float(performance.get('yield_percent', 0) or 0)
    sp_completion = float(performance.get('story_point_completion', 0) or 0)
    signoff_pct = round(approved / total * 100, 1) if total else 0.0
    readiness_pct = round(compliant / total * 100, 1) if total else 0.0
    risk_rows = []
    for r in rows:
        if not r.get('completed'):
            risk_rows.append(r)
    risk_rows.sort(key=lambda x: (0 if x.get('resolution_date') else -1, -(x.get('days_from_target') or 0)))
    return {
        'performance': performance, 'total': total, 'compliant': compliant, 'blocked': blocked,
        'approved': approved, 'yield_percent': yield_pct, 'story_point_completion': sp_completion,
        'ticket_score': ticket_score, 'hierarchy_score': hierarchy_score, 'signoff_percent': signoff_pct,
        'readiness_percent': readiness_pct, 'current_sp': round(current_sp,1), 'baseline_sp': round(baseline_sp,1),
        'scope_delta': scope_delta, 'scope_delta_percent': scope_delta_pct,
        'yield_rag': _rag(yield_pct, 90, 75), 'readiness_rag': _rag(readiness_pct, 90, 75),
        'hierarchy_rag': _rag(hierarchy_score, 90, 75), 'delivery_rag': _rag(sp_completion, 85, 65),
        'signoff_rag': _rag(signoff_pct, 100, 80), 'risk_rows': risk_rows[:8],
    }


@app.get('/executive', response_class=HTMLResponse)
def executive_dashboard(request: Request):
    require_login(request)
    arts = db.list_arts()
    art_id = int(request.query_params.get('art_id') or (arts[0]['id'] if arts else 0))
    art = db.get_art(art_id) if art_id else None
    try:
        allowed_days = max(0, min(int(request.query_params.get('allowed_days', '2')), 30))
    except (TypeError, ValueError):
        allowed_days = 2
    metrics = None; error = None; history = []
    if art and request.query_params.get('run') == '1':
        try:
            scan = _art_scan(art, force_refresh=True)
            baseline = db.latest_baseline(art_id, art['pi_value'])
            metrics = _executive_metrics(art, scan, baseline, allowed_days)
        except Exception as exc:
            error = str(exc)
    if art_id:
        history = db.latest_pi_performance_by_pi(art_id)
    return render(request, 'executive.html', {
        'arts': arts, 'art': art, 'metrics': metrics, 'history': history,
        'trend_points': _trend_points(history), 'allowed_days': allowed_days, 'error': error,
    })

@app.get('/art', response_class=HTMLResponse)
def art_page(request: Request):
    require_login(request)
    selected_id=int(request.query_params.get('art_id') or 0)
    return render(request,'art.html',{'arts':db.list_arts(),'selected':db.get_art(selected_id) if selected_id else None,'saved':request.query_params.get('saved')})

@app.post('/art')
def save_art_route(request: Request, name:str=Form(...), project:str=Form(...), pi_value:str=Form(...), priority:str=Form('Critical'),
                   scrum_master_ids:str=Form(...), scrum_master_names:str=Form(''), art_id:int=Form(0), csrf:str=Form(...)):
    require_login(request); verify_csrf(request,csrf)
    ids=[x.strip() for x in scrum_master_ids.replace('\n',',').split(',') if x.strip()]
    names=[x.strip() for x in scrum_master_names.replace('\n',',').split(',')]
    sms=[{'id':sid,'name':names[i] if i<len(names) and names[i] else sid} for i,sid in enumerate(ids)]
    saved_id=db.save_art({'id':art_id or None,'name':name.strip(),'project':project.strip().upper(),'pi_value':pi_value.strip(),'priority':priority.strip(),'scrum_masters':sms})
    clear_scan_cache(); return RedirectResponse(f'/art?art_id={saved_id}&saved=1',status_code=303)

@app.post('/art/{art_id}/delete')
def delete_art_route(request:Request, art_id:int, csrf:str=Form(...)):
    require_login(request); verify_csrf(request,csrf); db.delete_art(art_id); return RedirectResponse('/art',status_code=303)

@app.get('/pi-status', response_class=HTMLResponse)
def pi_status_page(request:Request):
    require_login(request); arts=db.list_arts(); art_id=int(request.query_params.get('art_id') or (arts[0]['id'] if arts else 0)); art=db.get_art(art_id) if art_id else None
    scan=None; rows=[]; baseline=None; error=None
    if art and request.query_params.get('run')=='1':
        try:
            scan=_art_scan(art, force_refresh=True)
            for result in scan['results']:
                row={'key':result['initiative']['key'],'summary':result['initiative'].get('summary',''),'scrum_master':result.get('art_scrum_master',''),
                     'size':result.get('initiative_size_code',''),'compliance':result.get('hierarchy_score',0)}
                row.update(_status_metrics(result)); rows.append(row)
            baseline=db.latest_baseline(art_id,art['pi_value'])
        except Exception as exc: error=str(exc)
    totals={'total_sp':sum(r['total_sp'] for r in rows),'done_sp':sum(r['done_sp'] for r in rows),'blocked':sum(r['blocked'] for r in rows)}
    totals['progress']=round(totals['done_sp']/totals['total_sp']*100,1) if totals['total_sp'] else 0
    return render(request,'pi_status.html',{'arts':arts,'art':art,'scan':scan,'rows':rows,'totals':totals,'baseline':baseline,'error':error})

@app.get('/initiative-explorer', response_class=HTMLResponse)
def initiative_explorer(request:Request):
    require_login(request); arts=db.list_arts(); art_id=int(request.query_params.get('art_id') or (arts[0]['id'] if arts else 0)); art=db.get_art(art_id) if art_id else None
    query=(request.query_params.get('q') or '').strip().upper(); matches=[]; error=None
    if art and query:
        try:
            scan=_art_scan(art)
            matches=[r for r in scan['results'] if query in r['initiative']['key'].upper() or query.casefold() in r['initiative'].get('summary','').casefold()]
        except Exception as exc:error=str(exc)
    return render(request,'explorer.html',{'arts':arts,'art':art,'query':query,'matches':matches,'error':error})

@app.get('/baseline', response_class=HTMLResponse)
def baseline_page(request:Request):
    require_login(request); arts=db.list_arts(); art_id=int(request.query_params.get('art_id') or (arts[0]['id'] if arts else 0)); art=db.get_art(art_id) if art_id else None
    baseline=db.latest_baseline(art_id,art['pi_value']) if art else None
    return render(request,'baseline.html',{'arts':arts,'art':art,'baseline':baseline,'saved':request.query_params.get('saved')})

@app.post('/baseline/{art_id}')
def capture_baseline(request:Request, art_id:int, csrf:str=Form(...)):
    require_login(request); verify_csrf(request,csrf); art=db.get_art(art_id)
    if not art: raise HTTPException(404,'ART not found')
    scan=_art_scan(art,force_refresh=True)
    snapshot={'art_name':art['name'],'pi_value':art['pi_value'],'initiatives':len(scan['results']),
              'story_points_total':scan['summary']['story_points_total'],
              'tickets':[{'key':r['initiative']['key'],'story_points':r.get('story_points_total',0)} for r in scan['results']]}
    db.save_baseline(art_id,art['pi_value'],snapshot)
    return RedirectResponse(f'/baseline?art_id={art_id}&saved=1',status_code=303)



def _is_completed_status(value: Any) -> bool:
    status = str(value or '').strip().casefold()
    return any(token in status for token in ('done', 'closed', 'resolved', 'complete', 'completed', 'released', 'accepted'))


def _parse_jira_date(value: Any):
    from datetime import datetime, date
    import re
    if value in (None, '', [], {}):
        return None
    # Some Jira/custom apps return a date inside an object rather than as a
    # scalar. Check the common keys before falling back to flattened text.
    if isinstance(value, dict):
        for key in ('date', 'value', 'startDate', 'endDate', 'targetDate', 'dueDate'):
            parsed = _parse_jira_date(value.get(key))
            if parsed:
                return parsed
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _parse_jira_date(item)
            if parsed:
                return parsed
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    # Accept ISO dates embedded in display values such as '30/Sep/26
    # (2026-09-30)' or vendor wrappers.
    match = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', text)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass
    for fmt in ('%d/%b/%y', '%d/%b/%Y', '%d-%b-%Y', '%d %b %Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _initiative_target_date(issue: dict[str, Any]):
    fields = issue.get('fields') or {}
    mapped_target_id = str(issue.get('_target_end_field_id') or 'customfield_10023')
    mapped_target_name = str(issue.get('_target_end_field_name') or 'Target end')
    candidates = [
        (f'{mapped_target_name} ({mapped_target_id})', fields.get(mapped_target_id)),
        (f'{mapped_target_name} ({mapped_target_id})', issue.get('target_end_date')),
        # Exact NMGOS fallback retained even when an older database does not yet
        # contain the new mapping setting.
        ('Target end (customfield_10023)', fields.get('customfield_10023')),
        ('Due date', fields.get('duedate')),
    ]
    for item in issue.get('_target_end_date_candidates', []) or []:
        if isinstance(item, dict):
            candidates.append((str(item.get('name') or item.get('field_id') or 'Target end date'), item.get('value')))
    # Support a Target end value written inside the Jira Description / ADF body,
    # for example: `Target end: 2026-07-20`. Some NMGOS screens render
    # structured initiative fields into the description rather than exposing
    # them as ordinary custom fields in search results.
    body = rich_text_to_text(fields.get('description'))
    if body:
        patterns = [
            r'(?im)^\s*target\s+end(?:\s+date)?\s*[:=-]\s*(20\d{2}-\d{2}-\d{2})\b',
            r'(?i)\btarget\s+end(?:\s+date)?\s*[:=-]\s*(20\d{2}-\d{2}-\d{2})\b',
            r'(?im)^\s*planned\s+end(?:\s+date)?\s*[:=-]\s*(20\d{2}-\d{2}-\d{2})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, body)
            if match:
                candidates.append(('Description / ADF: Target end', match.group(1)))
                break

    # Support explicitly mapped/additional field evidence and metadata shapes.
    for item in issue.get('_field_metadata', []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or item.get('field_id') or '')
        compact = ''.join(ch for ch in name.casefold() if ch.isalnum())
        if (('target' in compact and any(x in compact for x in ('end','finish','completion','due'))) or
            ('planned' in compact and any(x in compact for x in ('end','finish','completion')))):
            candidates.append((name, item.get('value')))
    seen=set()
    for label, raw in candidates:
        marker=(str(label), repr(raw))
        if marker in seen:
            continue
        seen.add(marker)
        parsed = _parse_jira_date(raw)
        if parsed:
            return parsed, label, str(raw)
    return None, '', ''


def _is_done_status(issue: dict[str, Any]) -> bool:
    fields = issue.get('fields') or {}
    status = fields.get('status') or issue.get('status') or {}
    if isinstance(status, dict):
        category = status.get('statusCategory') or {}
        category_name = str(category.get('name') or category.get('key') or '') if isinstance(category, dict) else str(category)
        status_name = str(status.get('name') or '')
    else:
        category_name = ''
        status_name = str(status)
    value = f'{category_name} {status_name}'.casefold()
    return any(token in value for token in ('done', 'complete', 'completed', 'closed', 'resolved', 'released', 'accepted'))


def _closed_date(issue: dict[str, Any]):
    """Return a defensible closure date for diagnostics/delivery health.

    Resolution date is authoritative. statuscategorychangedate is only a
    fallback when the issue is actually in Jira's Done category. This prevents
    Backlog/To Do transitions from being mislabeled as closure dates.
    """
    fields = issue.get('fields') or {}
    raw = fields.get('resolutiondate')
    parsed = _parse_jira_date(raw)
    if parsed:
        return parsed, 'Resolution date', str(raw)
    if _is_done_status(issue):
        raw = fields.get('statuscategorychangedate')
        parsed = _parse_jira_date(raw)
        if parsed:
            return parsed, 'Status category changed date (Done only)', str(raw)
    raw = issue.get('closed_date')
    parsed = _parse_jira_date(raw)
    if parsed and _is_done_status(issue):
        return parsed, 'Closed date', str(raw)
    return None, '', ''


def _initiative_resolution_date(issue: dict[str, Any]):
    """Return the Initiative's own Jira resolutiondate only.

    PI Yield is intentionally separated from hierarchy/delivery-health logic.
    Child tickets and statuscategorychangedate cannot make an Initiative pass.
    """
    fields = issue.get('fields') or {}
    raw = fields.get('resolutiondate')
    parsed = _parse_jira_date(raw)
    if parsed:
        return parsed, 'Resolution date', str(raw)
    return None, '', ''


def _yield_child_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return delivery-level descendants used to validate Initiative completion."""
    items=[]; seen=set()
    for issue in _all_work_items(result):
        if not issue or issue is result.get('initiative'):
            continue
        key=str(issue.get('key') or '')
        if not key or key in seen:
            continue
        seen.add(key)
        issue_type=str(issue.get('issue_type') or issue.get('issuetype') or '').casefold()
        # Epics/Features are containers. Stories, Tasks, Bugs, Spikes and other
        # leaf descendants are delivery work and must meet the date rule.
        if any(x in issue_type for x in ('epic','feature','initiative','capability')):
            continue
        items.append(issue)
    return items


def _initiative_yield_completion(result: dict[str, Any] | None, allowed_days: int = 2) -> dict[str, Any]:
    """Evaluate PI Yield using the top-level Initiative completion date.

    ASOC rule: a committed critical Initiative contributes to Yield when its own
    Jira resolution date is on or before the configured number of calendar days
    after Target end.
    Descendant Stories/Tasks remain delivery-health information only and do not
    determine the Yield result.
    """
    from datetime import timedelta

    empty = {
        'completed': False,
        'reason': 'Initiative not found in current Jira scope',
        'target_end_date': '',
        'target_date_source': '',
        'target_date_raw': '',
        'resolution_date': '',
        'resolution_date_source': '',
        'resolution_date_raw': '',
        'allowed_completion_date': '',
        'days_from_target': None,
        'total_children': 0,
        'closed_on_time': 0,
        'late_children': [],
        'open_children': [],
        'undated_children': [],
    }
    if not result:
        return empty

    initiative = result.get('initiative') or {}
    target, target_source, target_raw = _initiative_target_date(initiative)
    resolution, resolution_source, resolution_raw = _initiative_resolution_date(initiative)
    allowed_days = max(0, min(int(allowed_days), 30))
    allowed = target + timedelta(days=allowed_days) if target else None
    days_from_target = (resolution - target).days if target and resolution else None

    if not target:
        completed = False
        reason = 'Initiative has no usable Target end date'
    elif not resolution:
        completed = False
        reason = 'Initiative has no Jira resolution date'
    elif resolution <= allowed:
        completed = True
        if resolution <= target:
            reason = 'Initiative resolved on or before Target end'
        else:
            reason = f'Initiative resolved within the {allowed_days}-day tolerance ({days_from_target} day(s) after Target end)'
    else:
        completed = False
        reason = f'Initiative resolved {days_from_target} day(s) after Target end, outside the {allowed_days}-day tolerance'

    return {
        'completed': completed,
        'reason': reason,
        'target_end_date': target.isoformat() if target else '',
        'target_date_source': target_source,
        'target_date_raw': target_raw,
        'resolution_date': resolution.isoformat() if resolution else '',
        'resolution_date_source': resolution_source,
        'resolution_date_raw': resolution_raw,
        'allowed_completion_date': allowed.isoformat() if allowed else '',
        'days_from_target': days_from_target,
        'allowed_days': allowed_days,
        # Retained for template/backward compatibility. Child items no longer
        # influence PI Yield in v3.6.
        'total_children': 0,
        'closed_on_time': 0,
        'late_children': [],
        'open_children': [],
        'undated_children': [],
    }


def _pi_yield_metrics(art: dict[str, Any], scan: dict[str, Any], baseline: dict[str, Any] | None, allowed_days: int = 2) -> dict[str, Any]:
    current = {r['initiative']['key']: r for r in scan.get('results', [])}
    if baseline:
        committed_keys = [t.get('key') for t in baseline.get('snapshot', {}).get('tickets', []) if t.get('key')]
        baseline_date = baseline.get('created_at', '')
    else:
        committed_keys = list(current.keys())
        baseline_date = ''
    completed_keys = []; rows=[]; total_sp=0.0; done_sp=0.0
    for key in committed_keys:
        result=current.get(key)
        status=result.get('initiative',{}).get('status','Not found') if result else 'Not found in current scope'
        completion=_initiative_yield_completion(result, allowed_days=allowed_days)
        completed=bool(completion['completed'])
        if completed: completed_keys.append(key)
        m=_status_metrics(result) if result else {'total_sp':0,'done_sp':0,'progress':0,'health':'Missing','blocked':0}
        total_sp += float(m['total_sp']); done_sp += float(m['done_sp'])
        rows.append({'key':key,'summary':result.get('initiative',{}).get('summary','') if result else '', 'status':status,'completed':completed,'scrum_master':result.get('art_scrum_master','') if result else '', **completion, **m})
    committed=len(committed_keys); completed=len(completed_keys)
    return {'art_id':art['id'],'art_name':art['name'],'pi_value':art['pi_value'],'allowed_days':allowed_days,'committed_count':committed,'completed_count':completed,'remaining_count':max(committed-completed,0),'yield_percent':round(completed/committed*100,1) if committed else 0,'total_story_points':round(total_sp,2),'completed_story_points':round(done_sp,2),'story_point_completion':round(done_sp/total_sp*100,1) if total_sp else 0,'baseline_date':baseline_date,'used_baseline':bool(baseline),'rows':rows,'completed_keys':completed_keys,'committed_keys':committed_keys}

def _trend_points(history):
    return [{'pi': row['pi_value'], 'yield': float(row['yield_percent']), 'committed': int(row['committed_count']), 'completed': int(row['completed_count']), 'x': 50 + idx * 110} for idx, row in enumerate(history)]


@app.get('/pi-performance', response_class=HTMLResponse)
def pi_performance_page(request: Request):
    require_login(request)
    arts = db.list_arts()
    art_id = int(request.query_params.get('art_id') or (arts[0]['id'] if arts else 0))
    try:
        allowed_days = max(0, min(int(request.query_params.get('allowed_days', '2')), 30))
    except (TypeError, ValueError):
        allowed_days = 2
    art = db.get_art(art_id) if art_id else None
    metrics = None
    error = None
    baseline = None
    if art and request.query_params.get('run') == '1':
        try:
            scan = _art_scan(art, force_refresh=True)
            baseline = db.latest_baseline(art_id, art['pi_value'])
            metrics = _pi_yield_metrics(art, scan, baseline, allowed_days=allowed_days)
        except Exception as exc:
            error = str(exc)
    history = db.latest_pi_performance_by_pi(art_id) if art_id else []
    return render(request, 'pi_performance.html', {'arts': arts, 'art': art, 'metrics': metrics, 'baseline': baseline, 'history': history, 'trend_points': _trend_points(history), 'error': error, 'saved': request.query_params.get('saved'), 'allowed_days': allowed_days})


@app.post('/pi-performance/{art_id}/snapshot')
def save_pi_performance(request: Request, art_id: int, csrf: str = Form(...), allowed_days: int = Form(2)):
    require_login(request)
    verify_csrf(request, csrf)
    art = db.get_art(art_id)
    if not art:
        raise HTTPException(404, 'ART not found')
    scan = _art_scan(art, force_refresh=True)
    baseline = db.latest_baseline(art_id, art['pi_value'])
    allowed_days = max(0, min(int(allowed_days), 30))
    executive = _executive_metrics(art, scan, baseline, allowed_days)
    metrics = executive['performance']
    metrics.update({
        'readiness_percent': executive.get('readiness_percent', 0),
        'ticket_score': executive.get('ticket_score', 0),
        'hierarchy_score': executive.get('hierarchy_score', 0),
        'signoff_percent': executive.get('signoff_percent', 0),
        'blocked_count': executive.get('blocked', 0),
        'baseline_story_points': executive.get('baseline_sp', 0),
        'current_story_points': executive.get('current_sp', 0),
        'scope_delta': executive.get('scope_delta', 0),
        'scope_delta_percent': executive.get('scope_delta_percent', 0),
    })
    db.save_pi_performance_snapshot(art_id, art['pi_value'], metrics)
    return RedirectResponse(f'/pi-performance?art_id={art_id}&run=1&saved=1&allowed_days={allowed_days}', status_code=303)



def _analytics_snapshot_rows(art_id: int | None = None, limit_mode: str = '6') -> list[dict[str, Any]]:
    """Return the latest saved snapshot for each PI.

    When art_id is omitted, snapshots are aggregated across all configured ARTs.
    This is required for multi-PI trending because each PI is commonly represented
    by a separate ART configuration record.
    """
    latest: dict[str, dict[str, Any]] = {}
    art_lookup = {int(a['id']): a for a in db.list_arts()}
    for row in db.list_pi_performance_snapshots(art_id):
        item = dict(row)
        art = art_lookup.get(int(item.get('art_id') or 0), {})
        item['art_name'] = art.get('name', '')
        item['project'] = art.get('project', '')
        pi_key = str(item.get('pi_value') or '')
        # Portfolio mode deliberately keeps the latest saved result for each PI.
        # A selected ART keeps its own latest result per PI.
        existing = latest.get(pi_key)
        if not existing or str(item.get('created_at', '')) >= str(existing.get('created_at', '')):
            latest[pi_key] = item
    rows = list(latest.values())
    rows.sort(key=lambda r: (str(r.get('pi_value') or ''), str(r.get('created_at') or '')))
    if limit_mode in {'3', '6', '12'}:
        rows = rows[-int(limit_mode):]
    return rows


def _analytics_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    scrum: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        snap = row.get('snapshot') or {}
        committed = int(row.get('committed_count') or 0)
        completed = int(row.get('completed_count') or 0)
        total_sp = float(row.get('total_story_points') or 0)
        done_sp = float(row.get('completed_story_points') or 0)
        yield_pct = float(row.get('yield_percent') or 0)
        detail_rows = snap.get('rows') or []
        late = sum(1 for item in detail_rows if not item.get('completed') and item.get('resolution_date'))
        open_count = sum(1 for item in detail_rows if not item.get('resolution_date'))
        resolved_deltas = [float(item['days_from_target']) for item in detail_rows if item.get('days_from_target') is not None]
        avg_delay = round(sum(resolved_deltas) / len(resolved_deltas), 1) if resolved_deltas else 0
        point = {
            'pi': row.get('pi_value', ''), 'yield': round(yield_pct, 1),
            'committed': committed, 'completed': completed, 'remaining': max(committed-completed, 0),
            'total_sp': round(total_sp, 1), 'done_sp': round(done_sp, 1),
            'sp_completion': round((done_sp/total_sp*100), 1) if total_sp else 0,
            'readiness': float(snap.get('readiness_percent') or 0),
            'ticket_score': float(snap.get('ticket_score') or 0),
            'hierarchy': float(snap.get('hierarchy_score') or 0),
            'signoff': float(snap.get('signoff_percent') or 0),
            'blocked': int(snap.get('blocked_count') or 0),
            'scope_delta': float(snap.get('scope_delta') or 0),
            'scope_delta_percent': float(snap.get('scope_delta_percent') or 0),
            'allowed_days': int(snap.get('allowed_days') or 0),
            'late': late, 'open': open_count, 'avg_delay': avg_delay,
            'created_at': row.get('created_at', ''), 'x': 60 + idx * 115,
        }
        points.append(point)
        for item in detail_rows:
            name = str(item.get('scrum_master') or 'Unassigned')
            bucket = scrum.setdefault(name, {'name': name, 'committed': 0, 'completed': 0, 'total_sp': 0.0, 'done_sp': 0.0})
            bucket['committed'] += 1
            bucket['completed'] += 1 if item.get('completed') else 0
            bucket['total_sp'] += float(item.get('total_sp') or 0)
            bucket['done_sp'] += float(item.get('done_sp') or 0)
    scrum_rows = []
    for value in scrum.values():
        value['yield'] = round(value['completed']/value['committed']*100, 1) if value['committed'] else 0
        value['sp_completion'] = round(value['done_sp']/value['total_sp']*100, 1) if value['total_sp'] else 0
        value['total_sp'] = round(value['total_sp'], 1); value['done_sp'] = round(value['done_sp'], 1)
        scrum_rows.append(value)
    scrum_rows.sort(key=lambda x: (-x['yield'], x['name']))
    yields = [p['yield'] for p in points]
    return {
        'points': points, 'scrum_rows': scrum_rows,
        'average_yield': round(sum(yields)/len(yields), 1) if yields else 0,
        'best': max(points, key=lambda p: p['yield']) if points else None,
        'worst': min(points, key=lambda p: p['yield']) if points else None,
        'average_sp': round(sum(p['total_sp'] for p in points)/len(points), 1) if points else 0,
        'average_scope_delta': round(sum(p['scope_delta'] for p in points)/len(points), 1) if points else 0,
    }


@app.get('/analytics', response_class=HTMLResponse)
def analytics_page(request: Request):
    require_login(request)
    arts = db.list_arts()
    raw_art_id = str(request.query_params.get('art_id') or 'all')
    range_mode = str(request.query_params.get('range') or '6')
    art_id = int(raw_art_id) if raw_art_id.isdigit() else None
    art = db.get_art(art_id) if art_id else None
    rows = _analytics_snapshot_rows(art_id, range_mode)
    model = _analytics_model(rows)
    return render(request, 'analytics.html', {
        'arts': arts, 'art': art, 'art_filter': raw_art_id,
        'range_mode': range_mode, 'rows': rows, **model,
    })


@app.get('/analytics/export.csv')
def analytics_export_csv(request: Request):
    require_login(request)
    raw_art_id = str(request.query_params.get('art_id') or 'all')
    art_id = int(raw_art_id) if raw_art_id.isdigit() else None
    range_mode = str(request.query_params.get('range') or 'all')
    art = db.get_art(art_id) if art_id else None
    model = _analytics_model(_analytics_snapshot_rows(art_id, range_mode))
    out = io.StringIO(); writer = csv.writer(out)
    writer.writerow(['ART','PI','Yield %','Committed','Completed','Remaining','Total SP','Completed SP','SP Completion %','Readiness %','Ticket Compliance %','Hierarchy %','Manager Sign-Off %','Blocked','Scope Delta SP','Scope Delta %','Allowed Days','Late','Open','Average Days From Target','Saved At'])
    for p in model['points']:
        writer.writerow([(art['name'] if art else 'All ARTs'),p['pi'],p['yield'],p['committed'],p['completed'],p['remaining'],p['total_sp'],p['done_sp'],p['sp_completion'],p['readiness'],p['ticket_score'],p['hierarchy'],p['signoff'],p['blocked'],p['scope_delta'],p['scope_delta_percent'],p['allowed_days'],p['late'],p['open'],p['avg_delay'],p['created_at']])
    return Response(out.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="pi_performance_analytics_{(art['pi_value'] if art else 'portfolio')}.csv"'})


def _diagnostic_target_date_report(issue_key: str) -> dict[str, Any]:
    """Collect the exact Jira payload and date evidence needed to debug PI Yield.

    This deliberately performs a single full issue lookup plus a bounded child
    hierarchy scan. It is user-invoked only and is not part of normal portfolio
    scanning, so it does not slow the application down.
    """
    key = str(issue_key or '').strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]+-\d+', key):
        raise JiraError('Enter a valid Jira key, for example NMGOS-3919.')

    cfg = field_config()
    catalog = {str(f.get('id')): f for f in cfg.get('fields', []) if f.get('id')}
    mapped_id = str(cfg.get('mapping', {}).get('target_end') or 'customfield_10023')
    mapped_name = str((catalog.get(mapped_id) or {}).get('name') or 'Target end')

    jira.reset_metrics()
    issue = jira.issue(key, fields=['*all'])
    fields = issue.get('fields') or {}
    issue['_target_end_field_id'] = mapped_id
    issue['_target_end_field_name'] = mapped_name

    target, source, raw = _initiative_target_date(issue)
    description_text = rich_text_to_text(fields.get('description'))

    field_rows = []
    for field_id, value in fields.items():
        meta = catalog.get(str(field_id)) or {}
        name = str(meta.get('name') or field_id)
        compact = ''.join(ch for ch in name.casefold() if ch.isalnum())
        relevant = (
            field_id in {mapped_id, 'customfield_10023', 'duedate', 'resolutiondate', 'statuscategorychangedate', 'description'}
            or any(token in compact for token in ('targetend', 'targetfinish', 'targetcompletion', 'plannedend', 'plannedfinish', 'plannedcompletion'))
        )
        if not relevant:
            continue
        parsed = _parse_jira_date(value)
        field_rows.append({
            'field_id': str(field_id),
            'field_name': name,
            'raw_value': value,
            'display_value': rich_text_to_text(value) if isinstance(value, (dict, list)) else str(value or ''),
            'parsed_date': parsed.isoformat() if parsed else '',
        })

    # Bounded child scan: enough to expose status/resolution date and hierarchy
    # issues without running the expensive portfolio scanner.
    child_fields = ['summary', 'status', 'issuetype', 'parent', 'resolutiondate', 'statuscategorychangedate', mapped_id]
    descendants = []
    frontier = [key]
    seen = {key}
    for depth in range(1, 5):
        next_frontier = []
        for parent_key in frontier:
            try:
                children = jira.search(f'parent = {jira.jql_quote(parent_key)}', fields=child_fields, max_results=500)
            except Exception as exc:
                descendants.append({'parent': parent_key, 'depth': depth, 'error': str(exc)})
                continue
            for child in children:
                child_key = str(child.get('key') or '')
                if not child_key or child_key in seen:
                    continue
                seen.add(child_key)
                next_frontier.append(child_key)
                cfields = child.get('fields') or {}
                closed, closed_source, closed_raw = _closed_date(child)
                status_obj = cfields.get('status') or {}
                status_name = status_obj.get('name') if isinstance(status_obj, dict) else status_obj
                type_obj = cfields.get('issuetype') or {}
                type_name = type_obj.get('name') if isinstance(type_obj, dict) else type_obj
                descendants.append({
                    'key': child_key,
                    'parent': parent_key,
                    'depth': depth,
                    'summary': cfields.get('summary') or child.get('summary') or '',
                    'issue_type': type_name or child.get('issue_type') or '',
                    'status': status_name or child.get('status') or '',
                    'resolutiondate_raw': cfields.get('resolutiondate'),
                    'statuscategorychangedate_raw': cfields.get('statuscategorychangedate'),
                    'closed_date': closed.isoformat() if closed else '',
                    'closed_date_source': closed_source,
                    'closed_date_raw': closed_raw,
                })
        frontier = next_frontier
        if not frontier:
            break

    return {
        'diagnostic_version': APP_VERSION,
        'generated_at': datetime.now().astimezone().isoformat(),
        'issue_key': key,
        'jira_api_version': settings.jira_api_version,
        'configured_target_end': {'field_id': mapped_id, 'field_name': mapped_name},
        'detected_target_end': {
            'date': target.isoformat() if target else '',
            'source': source,
            'raw_value': raw,
        },
        'issue_summary': fields.get('summary') or '',
        'issue_type': ((fields.get('issuetype') or {}).get('name') if isinstance(fields.get('issuetype'), dict) else fields.get('issuetype')) or '',
        'issue_status': ((fields.get('status') or {}).get('name') if isinstance(fields.get('status'), dict) else fields.get('status')) or '',
        'target_candidate_fields': field_rows,
        'description_text': description_text,
        'descendants': descendants,
        'jira_api_requests': jira.request_count,
        'instructions': 'Download this JSON and provide it for debugging. It contains Jira issue field values but no Jira token or application password.',
    }


@app.get('/diagnostics', response_class=HTMLResponse)
def diagnostics_page(request: Request):
    require_login(request)
    key = str(request.query_params.get('issue_key') or '').strip()
    report = None
    error = None
    if key:
        try:
            report = _diagnostic_target_date_report(key)
            request.session['last_diagnostic_report'] = report
        except Exception as exc:
            error = str(exc)
    return render(request, 'diagnostics.html', {'issue_key': key, 'report': report, 'error': error})


@app.get('/diagnostics/download.json')
def diagnostics_download(request: Request, issue_key: str = ''):
    require_login(request)
    try:
        report = _diagnostic_target_date_report(issue_key) if issue_key else request.session.get('last_diagnostic_report')
        if not report:
            raise JiraError('Run a diagnostic first.')
    except Exception as exc:
        raise HTTPException(400, str(exc))
    filename = f"jira_yield_diagnostic_{report.get('issue_key','issue')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.get('/reports', response_class=HTMLResponse)
def reports_page(request:Request):
    require_login(request); return render(request,'reports.html',{})


@app.get('/export.csv')
def export_csv(request: Request):
    """Portfolio summary export retained for compatibility."""
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    filename = f'pi_readiness_summary_{filters["pi_value"]}_{_export_timestamp()}.csv'
    return _csv_response(_summary_export_rows(scan, filters), filename)


@app.get('/export-details.csv')
def export_details_csv(request: Request):
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    filename = f'pi_compliance_detail_{filters["pi_value"]}_{_export_timestamp()}.csv'
    return _csv_response(_detailed_export_rows(scan, filters), filename)


@app.get('/initiative/{initiative_key}/export.csv')
def export_initiative_csv(request: Request, initiative_key: str):
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    if not any(result['initiative']['key'] == initiative_key for result in scan['results']):
        raise HTTPException(status_code=404, detail='Ticket is not in the current filtered result set.')
    safe_key = ''.join(ch for ch in initiative_key if ch.isalnum() or ch in {'-', '_'})
    filename = f'{safe_key}_compliance_{filters["pi_value"]}_{_export_timestamp()}.csv'
    return _csv_response(_detailed_export_rows(scan, filters, initiative_key), filename)



def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.get('/export-summary.pdf')
def export_summary_pdf(request: Request):
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    filename = f'pi_compliance_summary_{filters["pi_value"]}_{_export_timestamp()}.pdf'
    return _pdf_response(build_summary_pdf(scan, filters, APP_VERSION), filename)


@app.get('/export-details.pdf')
def export_details_pdf(request: Request):
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    filename = f'pi_compliance_all_details_{filters["pi_value"]}_{_export_timestamp()}.pdf'
    return _pdf_response(build_detail_pdf(scan, filters, APP_VERSION), filename)


@app.get('/initiative/{initiative_key}/export.pdf')
def export_initiative_pdf(request: Request, initiative_key: str):
    require_login(request)
    filters = current_filters(request)
    scan = run_scan(filters)
    if not any(result['initiative']['key'] == initiative_key for result in scan['results']):
        raise HTTPException(status_code=404, detail='Ticket is not in the current filtered result set.')
    safe_key = ''.join(ch for ch in initiative_key if ch.isalnum() or ch in {'-', '_'})
    filename = f'{safe_key}_compliance_{filters["pi_value"]}_{_export_timestamp()}.pdf'
    return _pdf_response(build_detail_pdf(scan, filters, APP_VERSION, initiative_key), filename)
