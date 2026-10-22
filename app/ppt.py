"""Weekly RDE table renderer, with compatibility for old token templates.

The existing bot may continue to call render(template, output, report, rows, week, mode).
Monthly aggregation and the monthly timeline are deliberately not guessed here.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import math
import re
import unicodedata

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Inches, Pt

TOKENS = {"{{TITLE}}", "{{PERIOD}}", "{{COMPLETED}}", "{{NEXT_WEEK}}", "{{ISSUES}}", "{{MEMBERS}}"}


def frames(shapes):
    for shape in shapes:
        if shape.shape_type == 6:
            yield from frames(shape.shapes)
        if shape.has_text_frame:
            yield shape.text_frame
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame


def inspect_template(path):
    found = set()
    for slide in Presentation(str(path)).slides:
        for frame in frames(slide.shapes):
            found.update(re.findall(r"\{\{[A-Z_]+\}\}", frame.text))
    return found


def replace_paragraph(p, values):
    if not values:
        return
    runs = list(p.runs)
    text = "".join(r.text for r in runs)
    pattern = "|".join(re.escape(t) for t in values)
    for match in reversed(list(re.finditer(pattern, text))):
        start, end = match.span()
        value = values[match.group()]
        offset = 0
        for r in runs:
            old = r.text
            a, b = offset, offset + len(old)
            if b > start and a < end:
                prefix = old[:max(0, start - a)]
                suffix = old[max(0, end - a):] if end < b else ""
                r.text = prefix + (value if a <= start < b else "") + suffix
            offset = b


def _text(frame, value, size=None):
    """Preserve first paragraph/run style when replacing whole cell content."""
    p = frame.paragraphs[0]
    ppr = deepcopy(p._p.pPr) if p._p.pPr is not None else None
    rpr = deepcopy(p.runs[0]._r.rPr) if p.runs and p.runs[0]._r.rPr is not None else None
    frame.clear()
    for i, line in enumerate(str(value).split("\n")):
        target = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        if ppr is not None:
            old = target._p.pPr
            if old is not None:
                target._p.remove(old)
            target._p.insert(0, deepcopy(ppr))
        run = target.add_run()
        run.text = line
        if rpr is not None:
            run._r.insert(0, deepcopy(rpr))
        if size is not None:
            run.font.size = Pt(size)


def _table(slide, columns):
    candidates = [s for s in slide.shapes if s.has_table and len(s.table.columns) == columns]
    if len(candidates) != 1:
        raise ValueError(f"양식에서 {columns}열 표를 하나로 식별할 수 없습니다.")
    return candidates[0]


def _clone_slide(prs, source):
    """Keep native shape XML and relationships; supports supplied RDE templates."""
    dest = prs.slides.add_slide(source.slide_layout)
    mapping = {}
    for rel in source.part.rels.values():
        if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
            continue
        if rel.is_external:
            new_id = dest.part.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
        else:
            new_id = dest.part.rels.get_or_add(rel.reltype, rel.target_part)
        mapping[rel.rId] = new_id
    copied = deepcopy(source._element.cSld)
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    for el in copied.iter():
        for key, value in list(el.attrib.items()):
            if key.startswith(rel_ns) and value in mapping:
                el.set(key, mapping[value])
    dest._element.replace(dest._element.cSld, copied)
    # add_slide() initializes shapes while cloning layout placeholders.
    # Invalidate that cached shape tree after replacing cSld.
    dest.__dict__.pop("shapes", None)
    return dest


def _units(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _wrap(text, width):
    lines = []
    for paragraph in str(text).split("\n"):
        current, used = "", 0
        for ch in paragraph:
            cost = _units(ch)
            if current and used + cost > width:
                lines.append(current)
                current, used = "", 0
            current += ch
            used += cost
        lines.append(current)
    return lines


def _row_chunks(shape, rows, headers):
    """Conservative wrapping/pagination; never silently truncate cell content."""
    table = shape.table
    available = shape.height - sum(table.rows[i].height for i in range(headers))
    font = 10.0
    line_height = Pt(14)
    padding = Pt(9)
    max_lines = min(4, int((available - padding) // line_height))
    if max_lines < 1:
        raise ValueError("표의 데이터 영역이 너무 작습니다.")
    widths = [max(4, int((c.width / 12700 - 10) / (font * 0.56))) for c in table.columns]
    expanded = []
    for values in rows:
        wrapped = [_wrap(v, widths[i]) for i, v in enumerate(values)]
        count = max(math.ceil(len(lines) / max_lines) for lines in wrapped)
        for k in range(count):
            cells = []
            for lines in wrapped:
                # Repeat short identity metadata on a continuation row.
                part = lines if k and len(lines) <= max_lines else lines[k*max_lines:(k+1)*max_lines]
                cells.append("\n".join(part))
            height = max(Pt(29), padding + line_height * max(len(x.split("\n")) for x in cells))
            expanded.append((cells, int(height)))
    if not expanded:
        expanded = [(["미기재"] + [""] * (len(table.columns) - 1), int(Pt(29)))]
    pages, page, used = [], [], 0
    for entry in expanded:
        if page and used + entry[1] > available:
            pages.append(page)
            page, used = [], 0
        if entry[1] > available:
            raise ValueError("한 행을 넣을 공간이 부족합니다. 표 데이터 영역을 늘려 주세요.")
        page.append(entry)
        used += entry[1]
    if page:
        pages.append(page)
    return pages


def _fill_table(shape, entries, headers):
    table = shape.table
    if len(table.rows) <= headers:
        raise ValueError("스타일을 복사할 데이터 행이 양식에 필요합니다.")
    prototype = deepcopy(table.rows[headers]._tr)
    for tc in prototype.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}tc"):
        for attr in ("rowSpan", "gridSpan", "hMerge", "vMerge"):
            tc.attrib.pop(attr, None)
    for row in list(table.rows)[headers:]:
        table._tbl.remove(row._tr)
    for values, height in entries:
        tr = deepcopy(prototype)
        tr.set("h", str(height))
        table._tbl.append(tr)
        row = table.rows[len(table.rows) - 1]
        for i, value in enumerate(values):
            cell = row.cells[i]
            cell.margin_top = cell.margin_bottom = Pt(4)
            cell.text_frame.word_wrap = True
            _text(cell.text_frame, value, size=10)
            for p in cell.text_frame.paragraphs:
                p.space_before = p.space_after = Pt(0)
                p.line_spacing = Pt(14)
    shape.height = sum(row.height for row in table.rows)


def _caption(slide, week, mode):
    start = date.fromisoformat(week)
    period = f"실적 {start:%Y.%m.%d}~{start+timedelta(days=6):%m.%d} / 계획 {start+timedelta(days=7):%m.%d}~{start+timedelta(days=13):%m.%d}"
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        text = shape.text
        if text == "보고서 작성 예시":
            _text(shape.text_frame, "원문 테스트" if mode == "mock" else "검토용 초안")
        elif text.startswith("작성 예시"):
            _text(shape.text_frame, period)
        elif "차주 외근 일정" in text:
            _text(shape.text_frame, "03  차주 외근 일정")
    # Avoid shipping obsolete examples hidden in notes.
    if slide.has_notes_slide:
        tf = slide.notes_slide.notes_text_frame
        if tf is not None:
            tf.clear()


def _work_rows(report, business):
    groups = OrderedDict()
    for member in report.members:
        for task in member.work_items:
            if (task.category == "business") != business:
                continue
            label = task.project if business else (
                ("외부(요청사항)" if task.category == "external" else "내부(운영관리)")
                + "\n" + task.project
            )
            groups.setdefault(label, []).append(task)
    output = []
    for label, tasks in groups.items():
        # One task per row, on its actual/plan side. No arbitrary task matching.
        for task in tasks:
            payload = [task.description, task.assignee or "미기재", task.status or "미기재",
                       task.schedule or task.start_date or "미기재"]
            output.append([label] + (payload + [""] * 4 if task.phase == "actual" else [""] * 4 + payload))
    return output


def _visit_rows(report):
    return [[v.schedule or "미기재", v.project or "미분류", v.purpose,
             v.assignee or "미기재", v.status or "미기재", v.note]
            for member in report.members for v in member.visits]


def _issue_pages(report):
    lines = []
    for member in report.members:
        for issue in member.issue_items:
            lines.extend(_wrap(f"[{issue.project or '미분류'}] {issue.description}", 125))
            if issue.action:
                lines.extend(_wrap("대응: " + issue.action, 125))
            lines.append("")
    if not lines:
        lines = ["이슈 미기재 (이슈 없음으로 확인된 것은 아님)"]
    return [lines[i:i+18] for i in range(0, len(lines), 18)]


def render_weekly(template, output, report, rows, week, mode):
    """Render the supplied three-slide RDE weekly example without dummy data."""
    prs = Presentation(str(template))
    if len(prs.slides) != 3:
        raise ValueError("RDE 주간 양식은 원본 3페이지를 사용하세요.")
    sources = list(prs.slides)
    headings = ["\n".join(s.text for s in slide.shapes if s.has_text_frame) for slide in sources]
    if not ("사업 인프라 업무" in headings[0] and "사내 지원" in headings[1] and "이슈" in headings[2]):
        raise ValueError("전달받은 RDE 주간 양식의 페이지 구조와 다릅니다.")
    business_pages = _row_chunks(_table(sources[0], 9), _work_rows(report, True), 2)
    internal_pages = _row_chunks(_table(sources[1], 9), _work_rows(report, False), 2)
    visit_pages = _row_chunks(_table(sources[1], 6), _visit_rows(report), 1)
    for entries in business_pages:
        slide = _clone_slide(prs, sources[0])
        _caption(slide, week, mode)
        shape = _table(slide, 9)
        _text(shape.table.cell(0, 1).text_frame, "금주 실적")
        _text(shape.table.cell(0, 5).text_frame, "차주 계획")
        _fill_table(shape, entries, 2)
    empty_internal = _row_chunks(_table(sources[1], 9), [], 2)[0]
    empty_visits = _row_chunks(_table(sources[1], 6), [], 1)[0]
    for i in range(max(len(internal_pages), len(visit_pages))):
        slide = _clone_slide(prs, sources[1])
        _caption(slide, week, mode)
        internal, visits = _table(slide, 9), _table(slide, 6)
        _text(internal.table.cell(0, 1).text_frame, "금주 실적")
        _text(internal.table.cell(0, 5).text_frame, "차주 계획")
        _fill_table(internal, internal_pages[i] if i < len(internal_pages) else empty_internal, 2)
        _fill_table(visits, visit_pages[i] if i < len(visit_pages) else empty_visits, 1)
    for lines in _issue_pages(report):
        slide = _clone_slide(prs, sources[2])
        _caption(slide, week, mode)
        box = slide.shapes.add_textbox(Inches(0.5), Inches(1.8), Inches(12.3), Inches(5.1))
        box.name = "weekly_issue_body"
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = 0
        _text(frame, "\n".join(lines), size=14)
        for p in frame.paragraphs:
            p.space_before = Pt(0)
            p.space_after = Pt(0)
            p.line_spacing = Pt(18)
            for run in p.runs:
                run.font.name = "Noto Sans KR"
    # Remove the three untouched example slides, keeping only generated slides.
    for _ in range(3):
        sid = prs.slides._sldIdLst[0]
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))
    Presentation(str(output))
    return output


def _render_tokens(template, output, report, rows, week, mode):
    found = inspect_template(template)
    if not {"{{COMPLETED}}", "{{NEXT_WEEK}}", "{{ISSUES}}"}.issubset(found) or found - TOKENS:
        raise ValueError("기존 토큰 양식에 필수 토큰이 없거나 미지원 토큰이 있습니다.")
    names = {str(row["user_id"]): row["name"] for row in rows}
    def section(key):
        blocks = []
        for member in report.members:
            items = getattr(member, key)
            blocks.append(names[member.user_id] + "\n" +
                          ("\n".join("• " + item for item in items) if items else "미기재"))
        return "\n\n".join(blocks)
    start = date.fromisoformat(week)
    values = {
        "{{TITLE}}": "주간업무보고" + (" (원문 테스트)" if mode == "mock" else " (검토용 초안)"),
        "{{PERIOD}}": f"{start:%Y.%m.%d} ~ {start+timedelta(days=6):%Y.%m.%d}",
        "{{MEMBERS}}": ", ".join(names.values()),
        "{{COMPLETED}}": section("completed"), "{{NEXT_WEEK}}": section("next_week"),
        "{{ISSUES}}": section("issues"),
    }
    prs = Presentation(str(template))
    for slide in prs.slides:
        for frame in frames(slide.shapes):
            for paragraph in frame.paragraphs:
                replace_paragraph(paragraph, values)
    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))
    Presentation(str(output))
    return output


def render(template, output, report, rows, week, mode):
    """Existing bot entry point: recognizes RDE weekly and old token templates."""
    template, output = Path(template), Path(output)
    if not template.is_file():
        raise ValueError("templates/weekly-template.pptx 파일을 준비해 주세요.")
    expected = {str(row["user_id"]) for row in rows}
    actual = [m.user_id for m in report.members]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("작성자 목록이 원문과 다릅니다.")
    prs = Presentation(str(template))
    text = "\n".join(frame.text for slide in prs.slides for frame in frames(slide.shapes))
    if "월간업무" in text:
        raise ValueError("월간 양식은 월간 집계·타임표 구현 후 사용하세요. 현재는 주간만 지원합니다.")
    if "플랫폼인프라팀 주간업무" in text:
        return render_weekly(template, output, report, rows, week, mode)
    return _render_tokens(template, output, report, rows, week, mode)
