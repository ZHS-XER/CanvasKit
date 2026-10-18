from canvaskit.canvas import _next_link, _relative_folder, safe_name
from canvaskit.config import _read_env_file
from canvaskit.formats import clock, srt_time, to_srt
from canvaskit.video import (
    SubtitleLine,
    Transcript,
    jwt_from_url,
    classify_slide,
    lectures_from_records,
    parse_form,
    slides_from_data,
    transcript_from_data,
)


def test_next_link():
    header = (
        '<https://x/api/v1/courses?page=1&per_page=5>; rel="current",'
        '<https://x/api/v1/courses?page=2&per_page=5>; rel="next",'
        '<https://x/api/v1/courses?page=9&per_page=5>; rel="last"'
    )
    assert _next_link(header) == "https://x/api/v1/courses?page=2&per_page=5"
    assert _next_link('<https://x/a?page=9>; rel="last"') is None
    assert _next_link(None) is None


def test_relative_folder_and_safe_name():
    assert _relative_folder("course files") == ""
    assert _relative_folder("course files/2025/ppt") == "2025/ppt"
    assert _relative_folder("other") == "other"
    assert safe_name("a/b:c") == "a_b_c"
    assert safe_name('a?b*c"d<e>f|g.pdf', windows=False) == 'a?b*c"d<e>f|g.pdf'  # unchanged off Windows


def test_safe_name_windows_rules():
    assert safe_name('a?b*c"d<e>f|g.pdf', windows=True) == "a_b_c_d_e_f_g.pdf"
    assert safe_name("讲义 v2. ", windows=True) == "讲义 v2"  # trailing dot / space
    assert safe_name("con.pdf", windows=True) == "_con.pdf"
    assert safe_name("console.pdf", windows=True) == "console.pdf"
    assert safe_name("?", windows=True) == "_"


def test_env_file_with_bom(tmp_path):
    p = tmp_path / "env"
    p.write_bytes("\ufeffCANVAS_TOKEN=abc\r\n".encode("utf-8"))  # as written by Windows PowerShell 5
    assert _read_env_file(p) == {"CANVAS_TOKEN": "abc"}


def test_env_file(tmp_path):
    p = tmp_path / "env"
    p.write_text("# c\nexport CANVAS_TOKEN=abc==\nCANVAS_BASE_URL='https://x.y'\n")
    assert _read_env_file(p) == {"CANVAS_TOKEN": "abc==", "CANVAS_BASE_URL": "https://x.y"}
    assert _read_env_file(tmp_path / "missing") == {}


def test_parse_form_takes_first_form_only():
    html = """
    <html><body onload="document.forms[0].submit()">
    <form action="https://v.example/login" method="post">
      <input type="hidden" name="iss" value="https://canvas">
      <input name="empty">
      <input type="submit">
    </form>
    <form action="/other"><input name="x" value="1"></form>
    </body></html>"""
    action, method, fields = parse_form(html)
    assert action == "https://v.example/login"
    assert method == "POST"
    assert fields == {"iss": "https://canvas", "empty": ""}
    assert parse_form("<p>no form</p>") is None


def test_jwt_from_url_decodes_and_reads_fragment():
    assert jwt_from_url("https://v/ui/#/lms/launch?jwt_token=a%2Bb.c&x=1") == "a+b.c"
    assert jwt_from_url("https://v/ui/?other=1") is None


def test_lectures_numbering_and_title():
    recs = [
        {"id": 3, "courBeginTime": "2026-10-14 08:50:20", "courEndTime": "2026-10-14 09:45:00",
         "subjName": "高等数学", "weekNumber": 5, "week": 3, "letiNumber": 2},
        {"id": 2, "courBeginTime": "2026-10-14 07:55:00", "courEndTime": "2026-10-14 08:50:00",
         "subjName": "高等数学", "weekNumber": 5, "week": 3, "letiNumber": 1},
        {"id": 1, "courBeginTime": "2026-10-12 09:55:00", "courEndTime": "2026-10-12 10:50:00",
         "subjName": "高等数学", "weekNumber": 5, "week": 1, "letiNumber": 3, "teclTeacNames": ["张三"]},
    ]
    lec = lectures_from_records(recs)
    assert [x.id for x in lec] == [1, 2, 3]
    # Monday's lecture is period 3 but the first of that day.
    assert lec[0].title == "高等数学 第5周 周一 第1节"
    assert lec[0].teacher == "张三"
    assert lec[2].title == "高等数学 第5周 周三 第2节"


def test_transcript_and_srt():
    data = {
        "beforeAssemblyList": [
            {"bg": 8660, "ed": 31510, "res": "第二句，"},
            {"bg": 4050, "ed": 4330, "res": " hello，"},
            {"bg": 40000, "ed": 900000, "res": "很长的结束时间"},
            {"bg": 50000, "ed": 0, "res": "最后"},
            {"bg": 60000, "ed": 61000, "res": "  "},
        ],
        "afterAssemblyList": [{"bg": 4050, "ed": 248690, "res": "hello，第二句，"}],
    }
    t = transcript_from_data(data)
    assert [s.text for s in t.sentences] == ["hello，", "第二句，", "很长的结束时间", "最后"]
    srt = to_srt(t)
    assert srt.startswith("1\n00:00:04,050 --> 00:00:04,330\nhello，\n")
    assert "00:00:40,000 --> 00:00:50,000" in srt  # end clipped to the next cue
    assert "00:00:50,000 --> 00:00:52,000" in srt  # missing end gets a default
    assert srt.count("-->") == 4


def test_srt_falls_back_to_paragraphs():
    t = Transcript(paragraphs=[SubtitleLine(0, 1000, "段落")])
    assert "段落" in to_srt(t)


def test_time_formats():
    assert srt_time(3_723_004) == "01:02:03,004"
    assert clock(516) == "00:08:36"


def test_slides_sorted_hidden_dropped_and_cleaned():
    data = {"docList": [
        {"imageSeekTime": 516, "imageUrl": "b", "ocrText": " 简谐激励保存切换更多讨论 ", "textKeywordList": ["k", "保存"]},
        {"imageSeekTime": 1, "imageUrl": "a", "ocrText": "A"},
        {"imageSeekTime": 9, "imageUrl": "c", "showFlag": False},
    ]}
    s = slides_from_data(data, "2026-10-14 08:50:20")
    assert [x.image_url for x in s] == ["a", "b"]
    assert s[1].ocr_text == "简谐激励讨论" and s[1].keywords == ["k"]
    assert s[1].at == "08:58:56" and s[1].kind == "slideshow"
    assert slides_from_data(data)[0].at is None


def test_classify_editor_view():
    # OCR-like samples (made up); note the OCR typo 篇 for 第 and the glyph junk before the title.
    vib = ("日5回7-多元函数微分学(a) -张三-2026.pptx - PowerPoint搜索登录国X文件开始智慧树AI课堂助手插入"
           "...单击此处添加备注多元函数微分学幻灯片第1张，共116张B中文(中国)备注")
    assert classify_slide(vib) == ("editor", "7-多元函数微分学(a) -张三-2026", 1, 116)
    typo = "5.> =6-一元函数积分学-总结-张三-2026.pptx - PowerPoint口索登录...幻灯片篇18张，共19张以中文"
    assert classify_slide(typo) == ("editor", "6-一元函数积分学-总结-张三-2026", 18, 19)
    ela = "第1章质点运动学(交大版) - PowerPoint登录不文件开始插入...第一章 质点运动学幻灯片第6张，共42张2中文(中国)"
    assert classify_slide(ela) == ("editor", "第1章质点运动学(交大版)", 6, 42)


def test_classify_other_and_slideshow():
    desktop = "此电脑上海交通大学GoogleChrome回收站网络新学期新气象AMicrosoftEdge飞书"
    assert classify_slide(desktop)[0] == "other"
    assert classify_slide("多元函数微分学/偏导数与全微分")[0] == "slideshow"


def test_slides_brief_merges_and_strips_header():
    from canvaskit.formats import slides_brief
    from canvaskit.video import Lecture, Slide

    lec = Lecture(1, "大学物理", "", "", "2026-10-14 10:50:20", "2026-10-14 11:50:00", 5, 3, 3, 2)
    banner = "上海交通大学SHANGHAI JIAO TONG UNIVERSITY"
    slides = [
        Slide(1, "", banner + "动量定理的推导", ["动量定理"]),
        Slide(5, "", banner + "动量定理的推导", ["动量定理"]),
        Slide(60, "", banner + "角动量守恒 L=Iω", ["角动量"]),
        Slide(61, "", banner + "刚体的转动惯量", []),
        Slide(62, "", banner + "功和能", []),
        Slide(70, "", "此电脑", [], kind="other"),
        Slide(71, "", "回收站", [], kind="other"),
        Slide(90, "", "", [], kind="editor", deck="第5章 刚体力学(交大版)", slide_no=48, slide_total=48),
    ]
    text = slides_brief(slides, lec)
    lines = [l for l in text.splitlines() if l.startswith("[")]
    assert lines == [
        "[00:00:01] 放映 | 动量定理 | 动量定理的推导  (×2)",
        "[00:01:00] 放映 | 角动量 | 角动量守恒 L=Iω",
        "[00:01:01] 放映 |  | 刚体的转动惯量",
        "[00:01:02] 放映 |  | 功和能",
        "[00:01:10] 其他画面（桌面、浏览器等）  (×2)",
        "[00:01:30] 编辑视图 第5章 刚体力学(交大版) 第48/48张",
    ]
