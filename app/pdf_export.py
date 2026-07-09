from __future__ import annotations

import html
import io
import re
from datetime import datetime
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

VODACOM_RED = colors.HexColor('#E60000')
DARK = colors.HexColor('#1A1A1A')
MID = colors.HexColor('#666666')
LIGHT = colors.HexColor('#F4F5F7')
LINE = colors.HexColor('#D9DCE1')
GOOD = colors.HexColor('#138A4B')
BAD = colors.HexColor('#C62828')
WARN = colors.HexColor('#A66A00')


CRITERIA_LABELS = {
    'dor': 'Definition of Ready',
    'dod': 'Definition of Done',
    'acceptance_criteria': 'Acceptance Criteria',
    'dependencies': 'Known Dependencies',
    'story_estimation': 'Story Estimation / Sizing',
    'has_epics': 'Top-level Ticket Has Linked Epics',
    'epics_have_stories': 'Epics Have Stories',
}


def _clean(value: Any) -> str:
    text = '' if value is None else str(value)
    replacements = {
        '\u2013': '-', '\u2014': '-', '\u2212': '-', '\u2022': '*',
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2026': '...', '\u00a0': ' ', '\u2713': 'Pass', '\u2715': 'Fail',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    # Built-in Helvetica is portable on Windows/Render. Replace unsupported
    # glyphs rather than producing black boxes in the exported report.
    return text.encode('latin-1', errors='replace').decode('latin-1')


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    safe = html.escape(_clean(value)).replace('\n', '<br/>')
    return Paragraph(safe or '&nbsp;', style)


def _status(check: dict[str, Any]) -> tuple[str, colors.Color]:
    if check.get('excluded'):
        return 'EXCLUDED', WARN
    if not check.get('applicable', True):
        return 'N/A', MID
    if check.get('passed'):
        return 'PASS', GOOD
    return 'FAIL', BAD


def _criterion_labels(scan: dict[str, Any]) -> dict[str, str]:
    labels = dict(CRITERIA_LABELS)
    for criterion in (scan.get('config') or {}).get('additional_criteria', []) or []:
        criterion_id = str(criterion.get('id') or '').strip()
        if criterion_id:
            labels[f'custom:{criterion_id}'] = str(criterion.get('label') or criterion.get('field_name') or criterion_id)
    return labels


def _excluded_text(filters: dict[str, Any], scan: dict[str, Any]) -> str:
    values = filters.get('excluded_criteria') or []
    labels = _criterion_labels(scan)
    return ', '.join(labels.get(key, key) for key in values) or 'None'


def _page_decorator(canvas, doc, title: str, version: str) -> None:
    canvas.saveState()
    width, height = doc.pagesize
    canvas.setFillColor(VODACOM_RED)
    canvas.rect(0, height - 10 * mm, width, 10 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 8)
    canvas.drawString(14 * mm, height - 6.5 * mm, _clean(title)[:100])
    canvas.setFont('Helvetica', 7)
    canvas.drawRightString(width - 14 * mm, height - 6.5 * mm, f'Build v{_clean(version)}')

    canvas.setStrokeColor(LINE)
    canvas.line(14 * mm, 12 * mm, width - 14 * mm, 12 * mm)
    canvas.setFillColor(MID)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(14 * mm, 7 * mm, 'ASOC PI Readiness & Manager Sign-Off')
    canvas.drawRightString(width - 14 * mm, 7 * mm, f'Page {doc.page}')
    canvas.restoreState()


def _doc(buffer: io.BytesIO, page_size, title: str, version: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title=title,
        author='ASOC PI Readiness & Manager Sign-Off',
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    doc.addPageTemplates([
        PageTemplate(
            id='report',
            frames=[frame],
            onPage=lambda canvas, active_doc: _page_decorator(canvas, active_doc, title, version),
        )
    ])
    return doc


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        'title': ParagraphStyle(
            'ReportTitle', parent=sample['Title'], fontName='Helvetica-Bold',
            fontSize=20, leading=24, textColor=DARK, alignment=TA_LEFT, spaceAfter=7,
        ),
        'subtitle': ParagraphStyle(
            'ReportSubtitle', parent=sample['Normal'], fontName='Helvetica',
            fontSize=9, leading=13, textColor=MID, spaceAfter=10,
        ),
        'h1': ParagraphStyle(
            'H1', parent=sample['Heading1'], fontName='Helvetica-Bold',
            fontSize=14, leading=18, textColor=DARK, spaceBefore=4, spaceAfter=7,
        ),
        'h2': ParagraphStyle(
            'H2', parent=sample['Heading2'], fontName='Helvetica-Bold',
            fontSize=10.5, leading=14, textColor=VODACOM_RED, spaceBefore=5, spaceAfter=5,
        ),
        'body': ParagraphStyle(
            'Body', parent=sample['BodyText'], fontName='Helvetica',
            fontSize=8.2, leading=11, textColor=DARK,
        ),
        'small': ParagraphStyle(
            'Small', parent=sample['BodyText'], fontName='Helvetica',
            fontSize=7, leading=9, textColor=DARK,
        ),
        'tiny': ParagraphStyle(
            'Tiny', parent=sample['BodyText'], fontName='Helvetica',
            fontSize=6.2, leading=8, textColor=DARK,
        ),
        'center': ParagraphStyle(
            'Center', parent=sample['BodyText'], fontName='Helvetica-Bold',
            fontSize=7, leading=9, textColor=DARK, alignment=TA_CENTER,
        ),
        'header': ParagraphStyle(
            'Header', parent=sample['BodyText'], fontName='Helvetica-Bold',
            fontSize=6.6, leading=8, textColor=colors.white, alignment=TA_CENTER,
        ),
    }


def _metadata_story(scan: dict[str, Any], filters: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    excluded = _excluded_text(filters, scan)
    additional = (scan.get('config') or {}).get('additional_criteria', []) or []
    additional_text = ', '.join(
        str(item.get('label') or item.get('field_name') or item.get('field_id'))
        for item in additional
    ) or 'None'
    data = [
        [_p('PI', styles['small']), _p(filters.get('pi_value', ''), styles['small']),
         _p('Scrum Master', styles['small']), _p(filters.get('scrum_master_name') or filters.get('scrum_master_id', ''), styles['small'])],
        [_p('Project', styles['small']), _p(filters.get('project', ''), styles['small']),
         _p('Priority', styles['small']), _p(filters.get('priority', ''), styles['small'])],
        [_p('Excluded controls', styles['small']), _p(excluded, styles['small']),
         _p('Generated', styles['small']), _p(datetime.now().strftime('%Y-%m-%d %H:%M'), styles['small'])],
        [_p('Additional field controls', styles['small']), _p(additional_text, styles['small']),
         _p('Additional count', styles['small']), _p(len(additional), styles['small'])],
    ]
    table = Table(data, colWidths=[28 * mm, 55 * mm, 30 * mm, None], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT), ('BACKGROUND', (2, 0), (2, -1), LIGHT),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 0.35, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return [table, Spacer(1, 4 * mm), _p(f'Executed JQL: {scan.get("jql", "")}', styles['tiny']), Spacer(1, 4 * mm)]


def build_summary_pdf(scan: dict[str, Any], filters: dict[str, Any], version: str) -> bytes:
    buffer = io.BytesIO()
    styles = _styles()
    doc = _doc(buffer, landscape(A4), 'PI Compliance Portfolio Summary', version)
    story: list[Any] = [
        _p('PI Compliance Portfolio Summary', styles['title']),
        _p('High-level Manager Sign-Off view across all prioritised top-level tickets.', styles['subtitle']),
        *_metadata_story(scan, filters, styles),
    ]

    summary = scan.get('summary', {})
    kpis = [
        ('Top-level tickets', summary.get('initiatives', 0)),
        ('Hierarchy ready', summary.get('compliant', 0)),
        ('Blocked', summary.get('blocked', 0)),
        ('Current approvals', summary.get('approved', 0)),
        ('Avg ticket compliance', f"{summary.get('ticket_score', 0)}%"),
        ('Avg hierarchy compliance', f"{summary.get('hierarchy_score', 0)}%"),
        ('Rolled-up SP', summary.get('story_points_total', 0)),
    ]
    kpi_data = [[_p(label, styles['tiny']) for label, _ in kpis], [_p(value, styles['h1']) for _, value in kpis]]
    kpi_table = Table(kpi_data, colWidths=[doc.width / 7.0] * 7)
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT), ('BOX', (0, 0), (-1, -1), 0.5, LINE),
        ('INNERGRID', (0, 0), (-1, -1), 0.35, LINE), ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([kpi_table, Spacer(1, 5 * mm), _p('Portfolio results', styles['h1'])])

    headers = ['Ticket', 'Summary', 'Type', 'Epics', 'Stories', 'SP Roll-up', 'Ticket %', 'Hierarchy %', 'Failures', 'Sign-Off']
    rows: list[list[Any]] = [[_p(h, styles['header']) for h in headers]]
    for result in scan.get('results', []):
        root = result['initiative']
        signoff = result.get('latest_signoff') or {}
        signoff_text = signoff.get('decision', 'Pending')
        if signoff and not signoff.get('is_current'):
            signoff_text += ' (outdated)'
        rows.append([
            _p(root.get('key'), styles['small']), _p(root.get('summary'), styles['small']),
            _p(root.get('issue_type'), styles['small']), _p(result.get('epic_count'), styles['center']),
            _p(result.get('story_count'), styles['center']), _p(result.get('story_points_total', 0), styles['center']),
            _p(f"{result.get('ticket_score', 0)}%", styles['center']),
            _p(f"{result.get('hierarchy_score', 0)}%", styles['center']), _p(result.get('failure_count'), styles['center']),
            _p(signoff_text, styles['small']),
        ])
    col_widths = [23 * mm, 62 * mm, 22 * mm, 12 * mm, 12 * mm, 19 * mm, 18 * mm, 22 * mm, 15 * mm, 30 * mm]
    table = Table(rows, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.35, LINE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


def _iter_issues(result: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    root = result['initiative']
    yield 'Top-level ticket', '', root
    for story in result.get('direct_stories', []) or []:
        yield 'Direct Story', root.get('key', ''), story
    for item in result.get('additional_descendants', []) or []:
        yield 'Additional descendant work', root.get('key', ''), item
    for epic in result.get('epics', []):
        yield 'Epic', root.get('key', ''), epic
        for story in epic.get('stories', []):
            yield 'Story', epic.get('key', ''), story


def _criterion_table(checks: list[dict[str, Any]], styles: dict[str, ParagraphStyle], width: float) -> Table:
    rows: list[list[Any]] = [[
        _p('Criterion', styles['header']), _p('Result', styles['header']),
        _p('Evidence', styles['header']), _p('Remediation', styles['header'])
    ]]
    result_colors: list[tuple[int, colors.Color]] = []
    for index, check in enumerate(checks, start=1):
        status, status_color = _status(check)
        result_colors.append((index, status_color))
        remediation = check.get('remediation') if (
            check.get('applicable', True) and not check.get('excluded') and not check.get('passed')
        ) else ''
        rows.append([
            _p(check.get('label'), styles['small']), _p(status, styles['center']),
            _p(check.get('evidence'), styles['tiny']), _p(remediation, styles['tiny']),
        ])
    table = Table(rows, colWidths=[0.20 * width, 0.10 * width, 0.40 * width, 0.30 * width], repeatRows=1)
    commands = [
        ('BACKGROUND', (0, 0), (-1, 0), DARK), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.35, LINE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for row_index, status_color in result_colors:
        commands.extend([
            ('TEXTCOLOR', (1, row_index), (1, row_index), status_color),
            ('BACKGROUND', (0, row_index), (-1, row_index), colors.white if row_index % 2 else LIGHT),
        ])
    table.setStyle(TableStyle(commands))
    return table


def build_detail_pdf(
    scan: dict[str, Any], filters: dict[str, Any], version: str, initiative_key: str | None = None
) -> bytes:
    buffer = io.BytesIO()
    styles = _styles()
    title = 'PI Compliance Detailed Evidence Report'
    doc = _doc(buffer, A4, title, version)
    selected = [
        result for result in scan.get('results', [])
        if not initiative_key or result['initiative']['key'] == initiative_key
    ]
    story: list[Any] = [
        _p(title, styles['title']),
        _p('Complete criterion-level evidence for all Jira tickets in the selected compliance scope.', styles['subtitle']),
        *_metadata_story(scan, filters, styles),
    ]

    for result_index, result in enumerate(selected):
        if result_index:
            story.append(PageBreak())
        root = result['initiative']
        signoff = result.get('latest_signoff') or {}
        signoff_text = signoff.get('decision', 'Pending')
        if signoff and not signoff.get('is_current'):
            signoff_text += ' - outdated because Jira evidence changed'

        story.extend([
            _p(f"{root.get('key')} - {root.get('summary')}", styles['h1']),
            _p(
                f"Ticket compliance: {result.get('ticket_score', 0)}% | "
                f"Full hierarchy compliance: {result.get('hierarchy_score', 0)}% | "
                f"Epics: {result.get('epic_count', 0)} | Stories: {result.get('story_count', 0)} | "
                f"Story points roll-up: {result.get('story_points_total', 0)} "
                f"(Top: {result.get('initiative_story_points', 0)}, Epics: {result.get('epic_story_points', 0)}, Stories: {result.get('story_story_points', 0)}, Direct stories: {result.get('direct_story_points', 0)}, Other descendants: {result.get('additional_descendant_story_points', 0)}) | "
                f"Failures: {result.get('failure_count', 0)} | Sign-Off: {signoff_text}",
                styles['body'],
            ),
            Spacer(1, 2 * mm),
            _p('Hierarchy controls', styles['h2']),
            _criterion_table(result.get('structural_checks', []), styles, doc.width),
            Spacer(1, 4 * mm),
        ])

        for level, parent_key, item in _iter_issues(result):
            heading = f"{level}: {item.get('key')} - {item.get('summary')}"
            metadata = (
                f"Issue type: {item.get('issue_type')} | Status: {item.get('status') or 'Not set'} | "
                f"Assignee: {item.get('assignee') or 'Unassigned'} | Parent: {parent_key or 'None'} | "
                f"Own story points: {item.get('story_points', 0)} | Rolled-up story points: {item.get('rolled_story_points', item.get('story_points', 0))} | "
                f"Compliance: {item.get('score', 0)}% ({item.get('passed_count', 0)}/"
                f"{item.get('applicable_count', 0)} included controls passed)"
            )
            block = [
                CondPageBreak(62 * mm),
                _p(heading, styles['h2']),
                _p(metadata, styles['small']),
                Spacer(1, 1.5 * mm),
                _criterion_table(item.get('checks', []), styles, doc.width),
                Spacer(1, 4 * mm),
            ]
            story.append(block[0])
            story.append(KeepTogether(block[1:4]))
            story.extend(block[4:])

    if not selected:
        story.append(_p('No top-level tickets matched the selected scope.', styles['body']))
    doc.build(story)
    return buffer.getvalue()
