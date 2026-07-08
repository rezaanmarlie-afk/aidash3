# ASOC PI Readiness & Manager Sign-Off - Version 1.9

## Important upgrade verification

Version 1.9 adds Jira project-field discovery and configurable additional compliance criteria while retaining criterion exclusions, PDF exports and Jira Cloud Atlassian Document Format support for DoR, DOR, DoD and DOD sections.

After starting the app, confirm all three indicators:

1. The browser header displays **v1.9.0**.
2. The dashboard displays a black **Build v1.8.0** banner.
3. `http://127.0.0.1:8000/health` reports `"version": "1.9.0"` and the expected application folder.

The startup script now runs from its own folder, uses that folder's virtual environment, blocks startup when an old process already owns port 8000, and disables stale browser/proxy caching.


A self-contained FastAPI application that:

- Runs a configurable Jira query for prioritised Initiatives.
- Lets the manager select the **PI Priority (ASOC)** value and Scrum Master.
- Traverses **Initiative → Epic → Story** relationships.
- Checks Definition of Ready, Definition of Done, Acceptance Criteria, known dependencies and Story estimation.
- Prevents approval while any mandatory prerequisite is missing.
- Records approval or remediation decisions in SQLite with a cryptographic snapshot hash.
- Marks an old approval as outdated when the Jira evidence changes.
- Optionally writes the decision back to Jira as a comment and labels.
- Separates the selected ticket’s own compliance percentage from the complete hierarchy percentage used for Manager Sign-Off.
- Queries Jira for fields available to a selected project and lets managers add those fields as compliance controls.
- Exports portfolio summary, detailed criterion-level evidence, remediation and individual-ticket compliance to CSV and PDF.

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

Version 1.1 loaded children separately for each Initiative and then separately for each Epic. On a normal PI portfolio this could create hundreds of Jira requests and make the browser appear to run indefinitely.

The optimized scanner:

- Retrieves Initiative children in bulk.
- Retrieves Story children in bulk.
- Requests only fields required by the compliance engine instead of `*all`.
- Filters non-Initiative base-query matches in memory, without changing the working JQL.
- Batches issue-link fallback retrieval.
- Reuses a recent completed scan when opening an Initiative or exporting CSV.
- Shows elapsed time, Jira request count, base matches, skipped non-Initiatives, Epics and Stories after each scan.

The bulk controls can be adjusted in `.env`:

```env
JIRA_SCAN_MAX_RESULTS=2000
JIRA_SCAN_BATCH_SIZE=50
SCAN_CACHE_SECONDS=180
```

Keep the defaults initially. Increasing the maximum results can increase payload size; reducing the batch size creates more Jira requests.

## Compliance rules

Every Initiative, Epic and Story must contain evidence for:

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

- Each Initiative has at least one Epic.
- Every Epic has at least one Story.

## Upgrade an existing installation

This is a complete replacement package, not a patch or hotfix. Stop the running app, extract the ZIP, and copy the extracted application files over the existing application folder. The ZIP intentionally does not contain `.env` or `data/pi_readiness.db`, so your Jira credentials, field mappings, sign-offs and audit history are not overwritten. Start the app with `start.bat` and confirm the header shows **v1.9.0**.

## Run on a Windows laptop

1. Extract the ZIP.
2. Open PowerShell in the extracted folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start.ps1
```

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

A `render.yaml` is included.

1. Create a new GitHub repository and upload the extracted files.
2. In Render, create a Blueprint from the repository.
3. Set the secret environment variables shown as `sync: false`.
4. Attach the included persistent disk at `/var/data`.
5. Change `MOCK_MODE` to `false` after Jira connectivity is confirmed.

SQLite sign-off and audit data is stored under `DATA_DIR`; on Render this is `/var/data` so redeployments do not overwrite the records.

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

## Root-query handling

The configured base JQL is authoritative by default. The optional issue-type restriction is only applied when explicitly enabled in Settings, so organisation-specific types such as `Initiate` or `Signature Project` are not silently discarded.


## Version 1.4 — DoR / DoD alias recognition

The compliance engine recognises Definition of Ready as `Definition of Ready`, `DoR` or `DOR`, and Definition of Done as `Definition of Done`, `DoD` or `DOD`. Matching applies to dedicated Jira field names and clearly labelled description sections. Markdown, bullet, Jira wiki heading, inline and table forms are supported. Acronym headings are also treated as section boundaries so an empty DoR section cannot incorrectly inherit DoD evidence.


## Version 1.5 — ticket score, hierarchy score and detailed export

The application now reports two different measures:

- **Ticket compliance**: only the selected top-level Jira ticket. Each applicable control has equal weight. A non-Story with DoR, DoD and Acceptance Criteria present but no valid dependency declaration scores **75%** (3 of 4 controls).
- **Full hierarchy compliance**: the top-level ticket, all linked Epics and Stories, and both structural controls. This remains the score used to enable or block Manager Sign-Off.

Each Epic and Story also displays its own percentage and passed/applicable control count.

Exports available from the dashboard:

- **Export summary CSV**: one row per top-level ticket with both percentages and sign-off status.
- **Export detailed compliance**: one row per criterion, including hierarchy level, parent ticket, result, evidence, remediation, both scores and sign-off status.
- **Export this compliance**: detailed evidence for the selected ticket hierarchy from its inspection page.

The CSV output includes a UTF-8 BOM for Excel compatibility and protects Jira text from spreadsheet formula execution.

Known Dependencies can be sourced from a mapped Jira field or a clearly labelled `Dependencies` / `Known Dependencies` description section. A phrase such as `Technical dependency discovery` inside DoR does not count as a completed dependency declaration.

## Version 1.8 - criteria exclusions and PDF exports

The dashboard now provides a selectable exclusion list for:

- Definition of Ready
- Definition of Done
- Acceptance Criteria
- Known Dependencies
- Story Estimation / Sizing
- Top-level Ticket Has Linked Epics
- Epics Have Stories

Exclusions apply only to the current filtered review. An excluded control remains visible as **Excluded**, carries no scoring weight, does not create a failure and does not block Manager Sign-Off. The exclusion selection forms part of the compliance snapshot, CSV output and PDF evidence so the basis of approval remains auditable.

PDF exports available from the dashboard:

- **Export high-level PDF**: filter scope, exclusions, JQL, portfolio KPIs and one summary row per top-level ticket.
- **Export all ticket details PDF**: every top-level ticket, Epic and Story with status, assignee, score, criterion result, evidence, remediation, hierarchy controls and sign-off status.
- **Export this hierarchy PDF**: the same detailed evidence limited to the selected top-level ticket hierarchy.

PDF generation uses ReportLab and works locally, in Docker and on Render through the included `requirements.txt`.

## Version 1.9 - Jira project fields as compliance criteria

The **Settings** page now includes a Project Field Compliance Criteria section. Enter a Jira project key such as `NMGOS` and select **Load fields from Jira project**. The app queries project-scoped Jira metadata from a representative issue and Jira create metadata, then presents the resulting fields for selection. If Jira permissions prevent project-specific metadata retrieval, the screen clearly indicates that it has fallen back to the global Jira field catalogue.

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
