import copy
import csv
import hmac
import io
import json
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

from .compliance import ComplianceEngine, DEFAULT_FIELD_NAMES
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

    parent_link_field = jira.resolve_field(['Parent Link'])
    epic_link_field = jira.resolve_field(['Epic Link'])
    story_point_ids = story_point_field_candidates(fields, mapping)

    return {
        'mapping': mapping,
        'story_point_field_ids': story_point_ids,
        'story_point_field_names': [
            f"{(by_id.get(field_id) or {}).get('name', field_id)} ({field_id})"
            for field_id in story_point_ids
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
        'issuelinks', 'timetracking', 'parent',
        *cfg['mapping'].values(),
        *cfg.get('story_point_field_ids', []),
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

    epics_by_initiative, stories_by_epic, hierarchy_stats = jira.bulk_hierarchy(
        initiatives,
        fields=required_fields,
        parent_link_field_id=(cfg.get('parent_link_field') or {}).get('id'),
        epic_link_field_id=(cfg.get('epic_link_field') or {}).get('id'),
        max_results=settings.jira_scan_max_results,
    )

    engine_mapping = dict(cfg['mapping'])
    engine_mapping['story_points_candidates'] = cfg.get('story_point_field_ids', [])
    engine = ComplianceEngine(
        engine_mapping, cfg['internal_projects'], cfg['allow_description_fallback'],
        excluded_criteria=set(filters.get('excluded_criteria') or []),
        additional_criteria=cfg.get('additional_criteria', []),
    )
    results = []
    for initiative in initiatives:
        epics = epics_by_initiative.get(initiative['key'], [])
        scoped_stories = {
            epic['key']: stories_by_epic.get(epic['key'], [])
            for epic in epics
        }
        result = engine.evaluate_tree(initiative, epics, scoped_stories)
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
            'story_point_fields_requested': cfg.get('story_point_field_names', []),
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
    return RedirectResponse('/', status_code=303)


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
    restrict_initiative_type: str | None = Form(None),
    allow_description_fallback: str | None = Form(None),
    csrf: str = Form(...),
):
    require_login(request)
    verify_csrf(request, csrf)
    for key, value in {
        'pi_priority': pi_priority, 'scrum_master': scrum_master, 'dor': dor, 'dod': dod,
        'acceptance_criteria': acceptance_criteria, 'dependencies': dependencies,
        'story_points': story_points, 'squad': squad,
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
        'Epic Count', 'Story Count', 'Top-Level Story Points', 'Epic Story Points',
        'Story Story Points', 'Rolled-Up Story Points', 'Top-Level Failures', 'Hierarchy Failures',
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
            result.get('initiative_story_points', 0), result.get('epic_story_points', 0),
            result.get('story_story_points', 0), result.get('story_points_total', 0),
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
        'Latest Sign-Off', 'Sign-Off Current', 'JQL'
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
                ])

        add_issue(root, 'Top-level ticket')
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
            ])
    return rows


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
