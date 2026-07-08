import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from .settings import Settings


class JiraError(RuntimeError):
    pass


class JiraClient:
    def __init__(self, settings: Settings, mock_file: Path):
        self.settings = settings
        self.mock_file = mock_file
        self._mock = json.loads(mock_file.read_text(encoding='utf-8')) if settings.mock_mode else None
        self._fields_cache: list[dict] | None = None
        self._project_fields_cache: dict[str, dict[str, Any]] = {}
        self._request_count = 0

    @property
    def base(self) -> str:
        return self.settings.jira_base_url.rstrip('/')

    @property
    def request_count(self) -> int:
        return self._request_count

    def reset_metrics(self):
        self._request_count = 0

    def _counted(self, client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
        self._request_count += 1
        return client.request(method, url, **kwargs)

    def _client(self) -> httpx.Client:
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        auth = None
        if self.settings.jira_auth_mode.lower() == 'bearer':
            headers['Authorization'] = f'Bearer {self.settings.jira_api_token}'
        else:
            auth = (self.settings.jira_username, self.settings.jira_api_token)
        return httpx.Client(
            base_url=self.base,
            headers=headers,
            auth=auth,
            verify=self.settings.jira_verify_ssl,
            timeout=self.settings.jira_timeout_seconds,
            follow_redirects=True,
        )

    def _raise(self, response: httpx.Response):
        if response.is_success:
            return
        try:
            details = response.json()
        except Exception:
            details = response.text[:1000]
        raise JiraError(f'Jira API {response.status_code}: {details}')

    def fields(self, refresh: bool = False) -> list[dict]:
        if refresh:
            self._project_fields_cache.clear()
        if self._fields_cache is not None and not refresh:
            return self._fields_cache
        if self.settings.mock_mode:
            self._fields_cache = self._mock['fields']
            return self._fields_cache
        with self._client() as client:
            response = self._counted(client, 'GET', f'/rest/api/{self.settings.jira_api_version}/field')
            self._raise(response)
            self._fields_cache = response.json()
        return self._fields_cache

    @staticmethod
    def _normalise_project_field(field_id: str, metadata: dict, global_field: dict | None = None) -> dict:
        """Return a stable, UI-friendly Jira field description."""
        global_field = global_field or {}
        schema = metadata.get('schema') or global_field.get('schema') or {}
        allowed_values = metadata.get('allowedValues') or []
        return {
            'id': str(field_id),
            'name': str(metadata.get('name') or global_field.get('name') or field_id),
            'required': bool(metadata.get('required', False)),
            'schema_type': str(schema.get('type') or schema.get('custom') or ''),
            'custom': bool(str(field_id).startswith('customfield_') or global_field.get('custom')),
            'clauseNames': global_field.get('clauseNames') or metadata.get('clauseNames') or [],
            'allowed_values': [
                str(value.get('value') or value.get('name') or value.get('displayName') or value.get('id') or value)
                if isinstance(value, dict) else str(value)
                for value in allowed_values[:100]
            ],
        }

    @classmethod
    def _extract_create_meta_fields(cls, payload: dict, global_catalog: dict[str, dict]) -> list[dict]:
        """Extract and merge fields from legacy or current create-metadata responses."""
        found: dict[str, dict] = {}

        def add(field_id: str, metadata: dict):
            if not field_id:
                return
            current = found.get(field_id, {})
            merged = dict(current)
            merged.update(metadata or {})
            # A field can be optional on one issue type and required on another.
            merged['required'] = bool(current.get('required')) or bool((metadata or {}).get('required'))
            found[field_id] = merged

        for project in payload.get('projects', []) or []:
            for issue_type in project.get('issuetypes', []) or []:
                fields = issue_type.get('fields') or {}
                if isinstance(fields, dict):
                    for field_id, metadata in fields.items():
                        add(str(field_id), metadata or {})

        # Jira Cloud's current issue-type field endpoint returns values directly.
        values = payload.get('values') or []
        for item in values:
            field_id = item.get('fieldId') or item.get('key') or item.get('id')
            add(str(field_id or ''), item)

        return sorted(
            [cls._normalise_project_field(fid, meta, global_catalog.get(fid)) for fid, meta in found.items()],
            key=lambda item: (item['name'].casefold(), item['id']),
        )

    def project_fields(self, project_key: str, refresh: bool = False) -> dict[str, Any]:
        """Return fields available to a Jira project using project-scoped APIs.

        The catalogue is built from a representative issue's names/schema plus
        create metadata. This is more accurate than presenting every global
        custom field in a large Jira estate. When Jira permissions block both
        project-scoped sources, the method safely falls back to /field.
        """
        project = str(project_key or '').split(',')[0].strip()
        if not project:
            raise JiraError('A Jira project key is required to load project fields.')
        cache_key = project.upper()
        if not refresh and cache_key in self._project_fields_cache:
            return self._project_fields_cache[cache_key]

        global_fields = self.fields(refresh=refresh)
        global_catalog = {str(field.get('id')): field for field in global_fields if field.get('id')}
        if self.settings.mock_mode:
            result = {
                'project': project,
                'source': 'mock-project-metadata',
                'warning': '',
                'fields': sorted(
                    [self._normalise_project_field(fid, field, field) for fid, field in global_catalog.items()],
                    key=lambda item: (item['name'].casefold(), item['id']),
                ),
            }
            self._project_fields_cache[cache_key] = result
            return result

        api_version = self.settings.jira_api_version
        errors: list[str] = []
        discovered: dict[str, dict] = {}
        sources: list[str] = []

        def merge_fields(fields: list[dict]):
            for field in fields:
                field_id = str(field.get('id') or '')
                if not field_id:
                    continue
                current = discovered.get(field_id, {})
                merged = dict(current)
                merged.update(field)
                merged['required'] = bool(current.get('required')) or bool(field.get('required'))
                discovered[field_id] = merged

        # A representative issue exposes the names and schemas of fields that
        # are navigable on real tickets in this project, including fields that
        # are not placed on the Create screen.
        try:
            samples = self.search(
                f'project = {self.jql_quote(project)} ORDER BY updated DESC',
                fields=['summary'],
                max_results=1,
            )
            if samples:
                sample_key = samples[0].get('key')
                with self._client() as client:
                    response = self._counted(
                        client,
                        'GET',
                        f'/rest/api/{api_version}/issue/{quote(str(sample_key))}',
                        params={'fields': '*all', 'expand': 'names,schema'},
                    )
                    if response.is_success:
                        data = response.json()
                        names = data.get('names') or {}
                        schemas = data.get('schema') or {}
                        issue_fields = data.get('fields') or {}
                        ids = set(names) | set(schemas) | set(issue_fields)
                        issue_meta = []
                        for field_id in ids:
                            issue_meta.append(self._normalise_project_field(
                                str(field_id),
                                {'name': names.get(field_id), 'schema': schemas.get(field_id) or {}},
                                global_catalog.get(str(field_id)),
                            ))
                        merge_fields(issue_meta)
                        if issue_meta:
                            sources.append('project issue metadata')
                    else:
                        errors.append(f'project issue metadata returned HTTP {response.status_code}')
        except JiraError as exc:
            errors.append(f'project issue lookup failed: {exc}')

        legacy_fields: list[dict] = []
        with self._client() as client:
            response = self._counted(
                client,
                'GET',
                f'/rest/api/{api_version}/issue/createmeta',
                params={'projectKeys': project, 'expand': 'projects.issuetypes.fields'},
            )
            if response.is_success:
                legacy_fields = self._extract_create_meta_fields(response.json(), global_catalog)
                merge_fields(legacy_fields)
                if legacy_fields:
                    sources.append('project create metadata')
            else:
                errors.append(f'legacy create metadata returned HTTP {response.status_code}')

            # Use the current Jira Cloud endpoints only when the cheaper project
            # sources returned nothing. They can require one request per issue type.
            if api_version == 3 and not discovered:
                types_response = self._counted(
                    client,
                    'GET',
                    f'/rest/api/3/issue/createmeta/{quote(project)}/issuetypes',
                    params={'maxResults': 100},
                )
                if types_response.is_success:
                    issue_types = types_response.json().get('values', []) or []
                    merged_payload = {'values': []}
                    for issue_type in issue_types[:100]:
                        issue_type_id = issue_type.get('id')
                        if not issue_type_id:
                            continue
                        start_at = 0
                        while True:
                            fields_response = self._counted(
                                client,
                                'GET',
                                f'/rest/api/3/issue/createmeta/{quote(project)}/issuetypes/{quote(str(issue_type_id))}',
                                params={'startAt': start_at, 'maxResults': 100},
                            )
                            if not fields_response.is_success:
                                errors.append(
                                    f'issue type {issue_type_id} field metadata returned HTTP {fields_response.status_code}'
                                )
                                break
                            data = fields_response.json()
                            batch = data.get('values', []) or []
                            merged_payload['values'].extend(batch)
                            start_at += len(batch)
                            if not batch or start_at >= int(data.get('total', start_at)):
                                break
                    current_fields = self._extract_create_meta_fields(merged_payload, global_catalog)
                    merge_fields(current_fields)
                    if current_fields:
                        sources.append('project issue-type metadata')
                else:
                    errors.append(f'current create metadata returned HTTP {types_response.status_code}')

        if discovered:
            result = {
                'project': project,
                'source': ' + '.join(sources) or 'jira-project-metadata',
                'warning': '',
                'fields': sorted(discovered.values(), key=lambda item: (item['name'].casefold(), item['id'])),
            }
            self._project_fields_cache[cache_key] = result
            return result

        result = {
            'project': project,
            'source': 'global-field-fallback',
            'warning': (
                'Jira did not expose project-specific field metadata. Showing the global Jira field catalogue instead. '
                + ('; '.join(errors) if errors else '')
            ).strip(),
            'fields': sorted(
                [self._normalise_project_field(fid, field, field) for fid, field in global_catalog.items()],
                key=lambda item: (item['name'].casefold(), item['id']),
            ),
        }
        self._project_fields_cache[cache_key] = result
        return result

    def resolve_field(self, names: list[str]) -> dict | None:
        ordered_targets = [n.strip().lower() for n in names if n.strip()]
        fields = self.fields()
        for target in ordered_targets:
            for field in fields:
                clauses = [str(c).strip().lower() for c in field.get('clauseNames', []) or []]
                if target in clauses:
                    return field
            for field in fields:
                if str(field.get('name', '')).strip().lower() == target:
                    return field

        target = set(ordered_targets)
        for field in fields:
            name = str(field.get('name', '')).lower()
            if any(t in name or name in t for t in target if t):
                return field
        return None

    def field_catalog(self) -> dict[str, dict]:
        return {f.get('id'): f for f in self.fields() if f.get('id')}

    @staticmethod
    def jql_quote(value: str) -> str:
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

    @staticmethod
    def _chunks(values: Iterable[str], size: int) -> Iterable[list[str]]:
        bucket: list[str] = []
        for value in values:
            bucket.append(value)
            if len(bucket) >= size:
                yield bucket
                bucket = []
        if bucket:
            yield bucket

    @staticmethod
    def issue_type_name(issue: dict) -> str:
        return str(issue.get('fields', {}).get('issuetype', {}).get('name', '') or '')

    @staticmethod
    def _relation_value(issue: dict, custom_field_id: str | None) -> str:
        fields = issue.get('fields', {}) or {}
        parent = fields.get('parent')
        if isinstance(parent, dict) and parent.get('key'):
            return str(parent['key'])
        if not custom_field_id:
            return ''
        value = fields.get(custom_field_id)
        if isinstance(value, dict):
            return str(value.get('key') or value.get('value') or '')
        return str(value or '')

    @staticmethod
    def _linked_keys(issue: dict, expected_type: str) -> list[str]:
        found: list[str] = []
        for link in issue.get('fields', {}).get('issuelinks', []) or []:
            linked = link.get('outwardIssue') or link.get('inwardIssue') or {}
            key = str(linked.get('key') or '')
            type_name = str(linked.get('fields', {}).get('issuetype', {}).get('name', '') or '').lower()
            if key and (not type_name or expected_type.lower() in type_name):
                found.append(key)
        return found

    def build_jql(self, project: str, pi_field_clause: str, pi_value: str, priority: str,
                  scrum_master_clause: str, scrum_master_id: str) -> str:
        projects = [p.strip() for p in str(project).split(',') if p.strip()]
        project_values = ', '.join(self.jql_quote(p) for p in projects) or self.jql_quote(project)
        return (
            f'project in ({project_values}) '
            f'AND {self.jql_quote(pi_field_clause)} = {self.jql_quote(pi_value)} '
            f'AND priority = {self.jql_quote(priority)} '
            f'AND {self.jql_quote(scrum_master_clause)} = {self.jql_quote(scrum_master_id)} '
            f'ORDER BY key ASC'
        )

    def search(self, jql: str, fields: list[str] | None = None, max_results: int = 500) -> list[dict]:
        if self.settings.mock_mode:
            return self._mock['initiatives'][:max_results]
        selected = fields or ['*all']
        issues: list[dict] = []
        api_version = self.settings.jira_api_version
        with self._client() as client:
            if api_version == 3:
                token = None
                while len(issues) < max_results:
                    payload: dict[str, Any] = {
                        'jql': jql,
                        'fields': selected,
                        'maxResults': min(100, max_results - len(issues)),
                    }
                    if token:
                        payload['nextPageToken'] = token
                    response = self._counted(client, 'POST', '/rest/api/3/search/jql', json=payload)
                    if response.status_code in (404, 405, 410):
                        break
                    self._raise(response)
                    data = response.json()
                    batch = data.get('issues', [])
                    issues.extend(batch)
                    token = data.get('nextPageToken')
                    if not token or not batch:
                        return issues
            start_at = 0
            while len(issues) < max_results:
                payload = {
                    'jql': jql,
                    'fields': selected,
                    'startAt': start_at,
                    'maxResults': min(100, max_results - len(issues)),
                }
                response = self._counted(client, 'POST', f'/rest/api/{api_version}/search', json=payload)
                self._raise(response)
                data = response.json()
                batch = data.get('issues', [])
                issues.extend(batch)
                start_at += len(batch)
                if not batch or start_at >= int(data.get('total', len(issues))):
                    break
        return issues

    def _bulk_relation_search(
        self,
        parent_keys: list[str],
        legacy_clause: str,
        fields: list[str],
        max_results: int,
    ) -> list[dict]:
        if not parent_keys:
            return []
        seen: dict[str, dict] = {}
        batch_size = max(1, int(getattr(self.settings, 'jira_scan_batch_size', 50)))
        for batch in self._chunks(parent_keys, batch_size):
            key_list = ', '.join(self.jql_quote(key) for key in batch)
            modern = f'parent in ({key_list})'
            legacy = f'{self.jql_quote(legacy_clause)} in ({key_list})'
            combined = f'({modern} OR {legacy}) ORDER BY key ASC'
            try:
                for issue in self.search(combined, fields=fields, max_results=max_results):
                    seen[issue['key']] = issue
            except JiraError as combined_error:
                # A Jira instance may support either the modern parent field or
                # the legacy Parent Link/Epic Link field, but reject a query that
                # references the unsupported one. Run both valid clauses and
                # union their results rather than stopping after the first empty
                # successful query.
                successful_clause = False
                last_error: JiraError = combined_error
                for query in (f'{modern} ORDER BY key ASC', f'{legacy} ORDER BY key ASC'):
                    try:
                        for issue in self.search(query, fields=fields, max_results=max_results):
                            seen[issue['key']] = issue
                        successful_clause = True
                    except JiraError as exc:
                        last_error = exc
                        if not str(exc).startswith('Jira API 400:'):
                            raise
                if not successful_clause and not str(last_error).startswith('Jira API 400:'):
                    raise last_error
            if len(seen) >= max_results:
                break
        return list(seen.values())[:max_results]

    def fetch_by_keys(self, keys: Iterable[str], fields: list[str], max_results: int = 2000) -> list[dict]:
        unique_keys = list(dict.fromkeys(k for k in keys if k))
        seen: dict[str, dict] = {}
        batch_size = max(1, int(getattr(self.settings, 'jira_scan_batch_size', 50)))
        for batch in self._chunks(unique_keys, batch_size):
            key_list = ', '.join(self.jql_quote(key) for key in batch)
            for issue in self.search(f'key in ({key_list}) ORDER BY key ASC', fields=fields, max_results=len(batch)):
                seen[issue['key']] = issue
            if len(seen) >= max_results:
                break
        return list(seen.values())[:max_results]

    def bulk_hierarchy(
        self,
        initiatives: list[dict],
        fields: list[str],
        parent_link_field_id: str | None,
        epic_link_field_id: str | None,
        max_results: int = 2000,
    ) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, int]]:
        """Load the complete Initiative→Epic→Story hierarchy in bulk.

        The old implementation searched once per Initiative and once per Epic.
        This implementation normally uses one bulk Epic query and one bulk Story
        query, with issue-link fallback fetched in batches.
        """
        initiative_keys = [issue['key'] for issue in initiatives]
        epics_by_initiative: dict[str, list[dict]] = {key: [] for key in initiative_keys}
        stories_by_epic: dict[str, list[dict]] = {}

        if self.settings.mock_mode:
            for initiative_key in initiative_keys:
                epics = [
                    issue for issue in self._mock.get('children', {}).get(initiative_key, [])
                    if 'epic' in self.issue_type_name(issue).lower()
                ]
                epics_by_initiative[initiative_key] = epics
                for epic in epics:
                    stories_by_epic[epic['key']] = [
                        issue for issue in self._mock.get('children', {}).get(epic['key'], [])
                        if 'story' in self.issue_type_name(issue).lower()
                    ]
            return epics_by_initiative, stories_by_epic, {
                'epics_loaded': sum(len(v) for v in epics_by_initiative.values()),
                'stories_loaded': sum(len(v) for v in stories_by_epic.values()),
            }

        epic_candidates = self._bulk_relation_search(
            initiative_keys, 'Parent Link', fields, max_results=max_results
        )
        epic_by_key = {
            issue['key']: issue for issue in epic_candidates
            if 'epic' in self.issue_type_name(issue).lower()
        }

        for epic in epic_by_key.values():
            parent_key = self._relation_value(epic, parent_link_field_id)
            if parent_key in epics_by_initiative:
                epics_by_initiative[parent_key].append(epic)

        linked_epic_pairs: dict[str, list[str]] = {}
        missing_linked_epic_keys: list[str] = []
        for initiative in initiatives:
            linked = self._linked_keys(initiative, 'epic')
            linked_epic_pairs[initiative['key']] = linked
            for key in linked:
                if key not in epic_by_key:
                    missing_linked_epic_keys.append(key)
        for epic in self.fetch_by_keys(missing_linked_epic_keys, fields, max_results=max_results):
            if 'epic' in self.issue_type_name(epic).lower():
                epic_by_key[epic['key']] = epic
        for initiative_key, keys in linked_epic_pairs.items():
            present = {issue['key'] for issue in epics_by_initiative[initiative_key]}
            for key in keys:
                if key in epic_by_key and key not in present:
                    epics_by_initiative[initiative_key].append(epic_by_key[key])
                    present.add(key)

        all_epics = list({issue['key']: issue for values in epics_by_initiative.values() for issue in values}.values())
        epic_keys = [issue['key'] for issue in all_epics]
        stories_by_epic = {key: [] for key in epic_keys}

        story_candidates = self._bulk_relation_search(
            epic_keys, 'Epic Link', fields, max_results=max_results
        ) if epic_keys else []
        story_by_key = {
            issue['key']: issue for issue in story_candidates
            if 'story' in self.issue_type_name(issue).lower()
        }
        for story in story_by_key.values():
            epic_key = self._relation_value(story, epic_link_field_id)
            if epic_key in stories_by_epic:
                stories_by_epic[epic_key].append(story)

        linked_story_pairs: dict[str, list[str]] = {}
        missing_linked_story_keys: list[str] = []
        for epic in all_epics:
            linked = self._linked_keys(epic, 'story')
            linked_story_pairs[epic['key']] = linked
            for key in linked:
                if key not in story_by_key:
                    missing_linked_story_keys.append(key)
        for story in self.fetch_by_keys(missing_linked_story_keys, fields, max_results=max_results):
            if 'story' in self.issue_type_name(story).lower():
                story_by_key[story['key']] = story
        for epic_key, keys in linked_story_pairs.items():
            present = {issue['key'] for issue in stories_by_epic.get(epic_key, [])}
            for key in keys:
                if key in story_by_key and key not in present:
                    stories_by_epic.setdefault(epic_key, []).append(story_by_key[key])
                    present.add(key)

        for values in epics_by_initiative.values():
            values.sort(key=lambda issue: issue.get('key', ''))
        for values in stories_by_epic.values():
            values.sort(key=lambda issue: issue.get('key', ''))

        return epics_by_initiative, stories_by_epic, {
            'epics_loaded': len(all_epics),
            'stories_loaded': sum(len(values) for values in stories_by_epic.values()),
        }

    def children(self, parent_key: str, expected_type: str | None = None) -> list[dict]:
        """Compatibility method retained for external callers.

        Portfolio scans use bulk_hierarchy() and do not call this repeatedly.
        """
        if self.settings.mock_mode:
            children = self._mock.get('children', {}).get(parent_key, [])
            if expected_type:
                return [i for i in children if expected_type.lower() in self.issue_type_name(i).lower()]
            return children

        queries = [f'parent = {self.jql_quote(parent_key)}']
        if expected_type and expected_type.lower() == 'epic':
            queries.append(f'"Parent Link" = {self.jql_quote(parent_key)}')
        if expected_type and expected_type.lower() == 'story':
            queries.append(f'"Epic Link" = {self.jql_quote(parent_key)}')
        seen: dict[str, dict] = {}
        for query in queries:
            try:
                for issue in self.search(query, max_results=500):
                    type_name = self.issue_type_name(issue).lower()
                    if not expected_type or expected_type.lower() in type_name:
                        seen[issue['key']] = issue
            except JiraError:
                continue
        return list(seen.values())

    def issue(self, key: str, fields: list[str] | None = None) -> dict:
        if self.settings.mock_mode:
            for issue in self._mock['initiatives']:
                if issue['key'] == key:
                    return issue
            for children in self._mock.get('children', {}).values():
                for issue in children:
                    if issue['key'] == key:
                        return issue
            raise JiraError(f'Mock issue not found: {key}')
        with self._client() as client:
            response = self._counted(
                client, 'GET', f'/rest/api/{self.settings.jira_api_version}/issue/{quote(key)}',
                params={'fields': ','.join(fields or ['*all'])},
            )
            self._raise(response)
            return response.json()

    def search_users(self, query: str) -> list[dict]:
        if self.settings.mock_mode:
            q = query.lower()
            return [u for u in self._mock['users'] if q in u['displayName'].lower() or q in u['accountId'].lower()]
        with self._client() as client:
            response = self._counted(
                client, 'GET', f'/rest/api/{self.settings.jira_api_version}/user/picker',
                params={'query': query, 'maxResults': 20},
            )
            self._raise(response)
            data = response.json()
            return [
                {'accountId': u.get('accountId') or u.get('key') or u.get('name'), 'displayName': u.get('displayName')}
                for u in data.get('users', [])
            ]

    def add_comment(self, issue_key: str, text: str):
        if self.settings.mock_mode:
            return {'mock': True}
        with self._client() as client:
            if self.settings.jira_api_version == 3:
                body = {
                    'body': {
                        'type': 'doc', 'version': 1,
                        'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}],
                    }
                }
            else:
                body = {'body': text}
            response = self._counted(
                client, 'POST', f'/rest/api/{self.settings.jira_api_version}/issue/{quote(issue_key)}/comment', json=body
            )
            self._raise(response)
            return response.json()

    def add_labels(self, issue_key: str, labels: list[str]):
        if self.settings.mock_mode:
            return {'mock': True}
        operations = [{'add': label} for label in labels]
        with self._client() as client:
            response = self._counted(
                client, 'PUT', f'/rest/api/{self.settings.jira_api_version}/issue/{quote(issue_key)}',
                json={'update': {'labels': operations}},
            )
            self._raise(response)
        return {'ok': True}
