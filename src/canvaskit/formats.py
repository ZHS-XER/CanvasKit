"""Render transcripts and slide lists to files."""

from __future__ import annotations

import json
from collections import Counter

from .video import Lecture, Slide, Transcript


def srt_time(ms: int) -> str:
    h, rem = divmod(max(ms, 0), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def clock(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def to_srt(t: Transcript) -> str:
    lines = t.sentences or t.paragraphs
    out = []
    for i, line in enumerate(lines):
        end = line.end_ms
        nxt = lines[i + 1].begin_ms if i + 1 < len(lines) else None
        # Recognised end times are often far too long; never overlap the next cue.
        if nxt is not None and (end <= line.begin_ms or end > nxt):
            end = nxt
        if end <= line.begin_ms:
            end = line.begin_ms + 2000
        out.append(f"{i + 1}\n{srt_time(line.begin_ms)} --> {srt_time(end)}\n{line.text}\n")
    return "\n".join(out)


def to_text(t: Transcript, lecture: Lecture) -> str:
    """Compact paragraph transcript with timestamps; far fewer tokens than SRT."""
    header = (
        f"# {lecture.title}\n"
        f"# {lecture.begin} — {lecture.end}  {lecture.classroom}  {lecture.teacher}\n"
        f"# 自动语音识别结果，可能有错字\n\n"
    )
    body = "\n\n".join(f"[{clock(p.begin_ms / 1000)}] {p.text}" for p in (t.paragraphs or t.sentences))
    return header + body + "\n"


def slides_markdown(slides: list[Slide], lecture: Lecture, image_names: list[str] | None = None) -> str:
    out = [
        f"# {lecture.title} — 课堂 PPT 截图",
        f"",
        f"{lecture.begin} — {lecture.end}，共 {len(slides)} 张。[视频内偏移 / 实际时间]；OCR 为平台自动识别。",
        "类型：放映 = 全屏幻灯片；编辑视图 = PowerPoint 编辑界面（可读出讲稿名和页码）；其他 = 桌面、浏览器等。",
        "",
    ]
    kinds = {"slideshow": "放映", "editor": "编辑视图", "other": "其他"}
    for i, s in enumerate(slides, 1):
        where = ""
        if s.deck or s.slide_no:
            page = f" 第{s.slide_no}/{s.slide_total}张" if s.slide_no else ""
            where = f" — {s.deck or '?'}{page}"
        out.append(f"## {i:03}  [{clock(s.seek_seconds)} / {s.at or '?'}] {kinds.get(s.kind, s.kind)}{where}")
        if image_names:
            out.append(f"![]({image_names[i - 1]})")
        else:
            out.append(f"图片: {s.image_url}")
        if s.keywords:
            out.append(f"关键词: {'、'.join(s.keywords)}")
        if s.ocr_text:
            out.append(f"\n{s.ocr_text}")
        out.append("")
    return "\n".join(out)


def _common_header(texts: list[str], min_share: float = 0.5) -> str:
    """Prefix repeated on many different slides (school logo, deck banner), to be dropped from summaries.

    Counted over distinct texts so that one slide captured many times does not
    turn its own title into a "header"."""
    texts = list(dict.fromkeys(texts))
    if len(texts) < 4:
        return ""
    best = ""
    for k in range(6, 61):
        heads = Counter(t[:k] for t in texts if len(t) > k)
        if not heads:
            break
        head, n = heads.most_common(1)[0]
        if n < min_share * len(texts):
            break
        best = head
    return best


def slides_brief(slides: list[Slide], lecture: Lecture, width: int = 40) -> str:
    """One line per snapshot (time, kind, keywords, first words); repeats and non-slide screens merged."""
    kinds = {"slideshow": "放映", "editor": "编辑视图", "other": "其他"}
    header = _common_header([s.ocr_text for s in slides if s.kind == "slideshow"])
    out = [
        f"# {lecture.title} — 课堂截图摘要（{len(slides)} 张）",
        f"# {lecture.begin} — {lecture.end[11:]}；每行：[视频内偏移] 类型 | 关键词 | 开头文字（OCR，可能有错）",
        "",
    ]
    last_key = None
    repeat = 0
    for s in slides:
        if s.kind == "editor":
            page = f" 第{s.slide_no}/{s.slide_total}张" if s.slide_no else ""
            line, key = f"编辑视图 {s.deck or '?'}{page}", ("editor", s.deck, s.slide_no)
        elif s.kind == "other":
            line, key = "其他画面（桌面、浏览器等）", ("other",)
        else:
            text = s.ocr_text[len(header):] if header and s.ocr_text.startswith(header) else s.ocr_text
            text = " ".join(text.split())
            head = text[:width] + ("…" if len(text) > width else "")
            kw = "、".join(s.keywords[:5])
            line, key = f"放映 | {kw} | {head}", ("slideshow", kw, head[:15])
        if key == last_key:
            repeat += 1
            continue
        if repeat:
            out[-1] += f"  (×{repeat + 1})"
        repeat = 0
        last_key = key
        out.append(f"[{clock(s.seek_seconds)}] {line}")
    if repeat:
        out[-1] += f"  (×{repeat + 1})"
    return "\n".join(out) + "\n"


def slides_json(slides: list[Slide], lecture: Lecture) -> str:
    return json.dumps(
        {"lecture": lecture.to_dict(), "slides": [s.to_dict() for s in slides]},
        ensure_ascii=False,
        indent=1,
    )
