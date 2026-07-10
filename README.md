# ASOC PI Readiness & Manager Sign-Off App v1.21

Version 1.21 extends the Business Impact dependency rule: Known Dependencies now passes when Business Impact either says "No dependencies" with additional text, or documents an actual dependency as managed/tracked, for example "Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday". It also continues to scan duplicate or workspace-specific Business Impact fields.


## Important upgrade verification

Version 1.14 adds **deep descendant story point roll-up**: the app still requests the saved/global/dynamic Story Points fields, but it now follows Jira parent hierarchy, Parent Link, Epic Link and hierarchy-like issue links up to six levels deep. This means pointed Tasks, Features, Capabilities, directly linked Stories and other descendant delivery work can be reflected against the parent Initiative/top-level ticket even when they are not in the strict Initiative→Epic→Story structure. It retains Jira project-field criteria, criterion exclusions, PDF exports and Jira Cloud Atlassian Document Format support for DoR, DOR, DoD and DOD sections.

After starting the app, confirm all three indicators:

1. The browser header displays **v1.21.0**.
2. The dashboard displays a black **Build v1.21.0** banner.
3. `http://127.0.0.1:8000/health` reports `"version": "1.14.0"` and the expected application folder.

The startup script runs from its own folder, uses that folder's virtual environment, blocks startup when an old process already owns port 8000, and disables stale browser/proxy caching.

## What the app does

A self-contained FastAPI application that:

- Runs a configurable Jira query for prioritised top-level tickets.
- Lets the manager select the **PI Priority (ASOC)** value and Scrum Master.
- Traverses **top-level ticket → Epic → Story** relationships.
- Checks Definition of Ready, Definition of Done, Acceptance Criteria, known dependencies and Story estimation.
- Prevents approval while any mandatory prerequisite is missing.
- Records approval or remediation decisions in SQLite with a cryptographic snapshot hash.
- Marks an old approval as outdated when the Jira evidence changes.
- Optionally writes the decision back to Jira as a comment and labels.
- Separates the selected ticket's own compliance percentage from the complete hierarchy percentage used for Manager Sign-Off.
- Queries Jira for fields available to a selected project and lets managers add those fields as compliance controls.
- Rolls up the mapped Story Points / sizing field from Stories and Epics to the parent top-level ticket.
- Exports portfolio summary, detailed criterion-level evidence, remediation, story point roll-ups and individual-ticket compliance to CSV and PDF.

## Generated JQL

The app constructs the equivalent of:

```jql
project in ("NMGOS")
AND "PI Priority (ASOC)" = "PI26"
AND priority = "Critical"
AND "Scrum Master[User Picker (single user)]" = "70121:c296bec5-b136-48b7-9345-a1e16f9f38dc"
ORDER BY key ASC
```

The PI value, Scrum Master account ID, priority and project are configurable. The root query does not force an issue type by default; an optional Initiative issue-type restriction is available in Settings.

## Story point roll-up

Version 1.14 keeps story points separate from compliance scoring. Story points are informational and auditable; they do not add a pass/fail control unless you explicitly configure an additional Jira field criterion.

For each top-level ticket the app shows:

- Top-level ticket story points
- Epic story points
- Story-level story points
- Total rolled-up story points

For each Epic the app shows its own points plus the points from its linked Stories. For each Story the app shows its own mapped story point value.

The roll-up is included in:

- Dashboard KPIs
- Top-level ticket table
- Ticket detail page
- Summary CSV
- Detailed CSV
- High-level PDF
- Detailed PDF
- Manager Sign-Off snapshot hash

## Important Jira field fix

Jira can contain multiple custom fields with the same display name. This app explicitly queries:

```jql
"Scrum Master[User Picker (single user)]"
```

and does not substitute:

```jql
"Scrum Master[Dropdown]"
```

The JQL clause is stored independently from the selected field ID. This means an installation upgraded with an older SQLite settings database will still use the correct User Picker clause. The app also no longer adds `issuetype = "Initiative"` unless **Restrict root JQL to Initiative issue type** is enabled in Settings.

## Performance architecture

The optimized scanner:

- Retrieves top-level children in bulk.
- Retrieves Story children in bulk.
- Requests only fields required by the compliance engine instead of `*all`.
- Filters non-Initiative base-query matches in memory only when that option is enabled.
- Batches issue-link fallback retrieval.
- Reuses a recent completed scan when opening a ticket or exporting.
- Shows elapsed time, Jira request count, base matches, skipped non-Initiatives, Epics and Stories after each scan.

The bulk controls can be adjusted in `.env`:

```env
JIRA_SCAN_MAX_RESULTS=2000
JIRA_SCAN_BATCH_SIZE=50
SCAN_CACHE_SECONDS=180
```

Keep the defaults initially. Increasing the maximum results can increase payload size; reducing the batch size creates more Jira requests.

## Compliance rules

Every top-level ticket, Epic and Story must contain evidence for:

1. Definition of Ready
2. Definition of Done
3. Acceptance Criteria
4. Known dependencies

Stories must also have Story Points or a non-zero Jira original estimate.

Dependency evidence passes only when:

- The field explicitly says there are no known dependencies; or
- The dependency is described and a Jira issue is linked.

For Stories, linked dependency tickets must belong to one of the configured ASOC-internal Jira project keys.

The app also checks that:

- Each top-level ticket has at least one Epic.
- Every Epic has at least one Story.

## Upgrade an existing installation

This is a complete replacement package, not a patch or hotfix. Stop the running app, extract the ZIP, and copy the extracted application files over the existing application folder. The ZIP intentionally does not contain `.env` or `data/pi_readiness.db`, so your Jira credentials, field mappings, sign-offs and audit history are not overwritten. Start the app with `start.bat` and confirm the header shows **v1.21.0**.

## Run on a Windows laptop

1. Extract the ZIP.
2. Open PowerShell in the extracted folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start.ps1
```

Or run `start.bat`.

4. Open `http://127.0.0.1:8000`.
5. Demo login:
   - Username: `admin`
   - Password: `ChangeMe123!`

The first launch uses mock Jira data, so the complete workflow can be tested without Jira access.

## Connect to Jira

Copy `.env.example` to `.env` and update:

```env
MOCK_MODE=false
JIRA_BASE_URL=https://your-jira-host
JIRA_API_VERSION=3
JIRA_AUTH_MODE=basic
JIRA_USERNAME=your-email@example.com
JIRA_API_TOKEN=your-token
JIRA_VERIFY_SSL=true
APP_SECRET=replace-with-a-long-random-secret
APP_ADMIN_PASSWORD=replace-this-password
APP_MANAGER_NAME=Manager Full Name
```

For Jira Data Center using a Personal Access Token:

```env
JIRA_API_VERSION=2
JIRA_AUTH_MODE=bearer
JIRA_USERNAME=
JIRA_API_TOKEN=your-personal-access-token
```

If the corporate Jira certificate is intercepted by an internal proxy, install the corporate CA certificate rather than permanently disabling SSL verification. `JIRA_VERIFY_SSL=false` is available only for controlled troubleshooting.

## Configure Jira fields

After signing in:

1. Open **Settings**.
2. Map:
   - PI Priority (ASOC)
   - Scrum Master
   - Definition of Ready
   - Definition of Done
   - Acceptance Criteria
   - Dependencies
   - Story Points
3. Enter ASOC-internal project keys, for example `NMGOS, ASOC, OSS`.
4. Save.

The app reads Jira's field catalogue and stores the selected field IDs locally, avoiding hard-coded custom field numbers.

## Additional Jira field criteria

The **Settings** page includes a Project Field Compliance Criteria section. Enter a Jira project key such as `NMGOS` and select **Load fields from Jira project**. The app queries project-scoped Jira metadata from a representative issue and Jira create metadata, then presents the resulting fields for selection. If Jira permissions prevent project-specific metadata retrieval, the screen clearly indicates that it has fallen back to the global Jira field catalogue.

Each additional field can use one of these rules:

- Required / must be populated
- Must equal
- Must not equal
- Must contain
- Must be one of a comma-separated list
- Minimum numeric value
- Maximum numeric value
- Must be Yes / True / Complete

Each rule can apply to all ticket levels, top-level tickets only, Epics only or Stories only. Additional controls are requested in the bulk Jira scan, displayed as individual compliance checks, included in ticket and hierarchy percentages, available in the exclusion selector, and recorded in CSV, PDF and Manager Sign-Off snapshots.

Changing an additional criterion changes the compliance basis. A previous approval is therefore shown as outdated until the hierarchy is reviewed and signed off again.

## Jira hierarchy handling

The app searches hierarchy relationships in bulk through Jira's `parent` relationship. It also unions legacy `Parent Link` and `Epic Link` results and checks issue links as a batched fallback. This supports a mixture of current Jira Cloud hierarchy and older/company-specific Jira configurations without issuing a separate request for every ticket.

## Optional Jira write-back

Set:

```env
ENABLE_JIRA_WRITEBACK=true
```

The sign-off page will then offer a checkbox to:

- Add a Jira comment with the decision, score, signer and snapshot ID.
- Add labels such as `manager-signoff-approved` and `manager-signoff-pi26`.

The Jira integration account needs permission to browse issues, add comments and edit labels.

## Deploy to Render

A `render.yaml` is included for new services, but for an existing `aidash3.onrender.com` service configure the existing Render Web Service manually so the URL is preserved.

Native Python service settings:

```bash
Build Command: pip install -r requirements.txt
Start Command: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Health Check Path: /health
```

SQLite sign-off and audit data is stored under `DATA_DIR`; on Render use a persistent disk and set `DATA_DIR=/var/data/pisign`.

## Security notes

- Change the default password and application secret before non-demo use.
- Use a dedicated Jira service account with least privilege.
- Keep Jira tokens in environment variables, never in source control.
- Place the service behind the corporate access layer or an approved reverse proxy for production use.
- The local login is intentionally simple; corporate SSO/OIDC can be added when the approved identity-provider details are available.

## Test

```powershell
python -m pytest -q
```


## Version 1.14 story point fix

If some boards/workspaces still showed missing story points in Version 1.11, the cause was usually a team-managed or board-specific estimation custom field that was not discoverable from the global Jira field catalogue. Version 1.14 now performs a second-pass enrichment against the actual loaded issues using `fields=*all` with `expand=names,schema`. The compliance engine uses the manager-mapped Story Points field first, then global candidates, then issue-metadata candidates. Scan diagnostics show both the requested fields and the additional fields discovered from loaded issues.

## Version 1.14 update

- Story point roll-up now includes Stories linked directly to the top-level Initiative/ticket, not only Stories under Epics.
- Jira hierarchy diagnostics now separates nested Stories from direct Stories.
- Dashboard, detail view, CSV exports and PDF exports show direct Story counts/points where applicable.
- This resolves cases such as an Initiative with pointed Stories directly attached to it but no reflected Initiative roll-up.


## Version 1.14 update

- Adds a deep descendant roll-up scan for story points.
- Follows modern Jira `parent`, legacy `Parent Link`, legacy `Epic Link`, and hierarchy-like issue links.
- Rolls up story points from non-standard child work such as Tasks, Features, Capabilities or additional intermediate hierarchy levels.
- Keeps this broader descendant roll-up informational only; it does not change compliance scoring or Manager Sign-Off readiness.
- Dashboard diagnostics now show descendant issues loaded, roll-up depth and hierarchy-like linked issues included.
- Dashboard, detail view, CSV exports and PDF exports show additional descendant story points separately from normal Epic/Story points.


## v1.16 - Business Impact dependency fallback

Known Dependencies now passes when the mapped **Business Impact** field explicitly states that there are no dependencies, for example `No dependencies`, `No known dependencies`, or `Dependencies: None`. This is useful where ASOC initiatives record the dependency declaration in Business Impact rather than a dedicated Dependencies field.

## v1.15 - Initiative sizing

Initiative size is now derived from the final rolled-up Story Points total. The default size bands are XS <=20, S <=50, M <=100, L <=200, XL <=400 and XXL above 400. Thresholds are configurable in Settings and appear in the dashboard, CSV exports and PDF reports.


## Version 1.17 update

- Known Dependencies now passes whenever the mapped Business Impact field explicitly says there are no dependencies, even if the dedicated dependency field is blank, incomplete, or has no linked Jira dependency ticket.
- Bare Business Impact values like `None` still do not pass unless dependencies are mentioned explicitly.
