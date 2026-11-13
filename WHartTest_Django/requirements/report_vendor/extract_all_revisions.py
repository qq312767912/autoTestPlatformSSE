#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_all_revisions.py
========================

读取一个 .docx 文件中的**全部**修订（w:ins / w:del），把所有改动按
**原文档的标题层级**整合成一份新的 .docx 汇总报告。

与"只取最新一次修订"的版本不同：这里不做任何批次划分、不按作者/时间截断，
所有修订合并成一棵标题树；只要某个小节里有改动，就把该小节的**完整内容**
按原文顺序输出，改动在原文位置就地标注（redline 风格）。

标注规则：
    新增文字        绿色 + 浅绿底纹
    删除文字        灰色 + 删除线
    有改动的段落    左侧竖线 + 极浅底纹
    整行新增/删除   整行变色
    仅样式修订      不单独列出（字体、字号、加粗、行距、边框等）
    版本历史表      只列有改动的行，未改动的历史版本行省略
    审核记录表      只列有改动的行（审核 / 评审 / 会签 / 签署 / 审批记录、签署块）
    "文档审核记录"等模块  只输出改动内容，未改动的段落与表格行全部省略
    目录（TOC）     只保留目标小节有改动的条目，其余条目省略
    标题编号        自动编号（numbering.xml）还原为文字，如 "2.1.1"
    被参考章节      修订文字里写"参考 3.2 节 / 详见第 4 章 / 逻辑同 5.1"时，
                    在该小节后照录被引用章节的原文（不标注改动），便于对照

内容完整性：
- 图片 / 图形（内嵌图片、浮动图片、VML 图形、图表与 OLE 对象）会被搬进报告，
  对应的 media / charts / embeddings 部件与关系一并复制；浮动图片转为嵌入式
  排版，避免在报告中重叠
- 自动编号列表沿用源文档的 numbering.xml，段落上的 numPr（含从段落样式继承的）
  在报告中保留，编号不会丢失
- 表格单元格内的嵌套表格一并保留
- 超链接保留可点击

标题识别规则（多级兜底，兼容 Word / WPS）：
1) 段落直接带 w:outlineLvl
2) pStyle 在 styles.xml 中解析：样式名 / styleId 命中 Title、Heading N、标题 N
3) 沿 basedOn 链继承层级
4) WPS 常见的纯数字 styleId（如 "1"、"2"）
5) 全文档都没有可识别的标题样式时，才用文本形态猜：
   "第X章 / 第X篇" -> 1 级；"2.1.1 xxx" 这类编号短行 -> 按编号段数定级

特点：
- 纯 Python 标准库（zipfile + xml.etree + argparse），无需安装任何第三方包
- Windows 下可直接双击运行，或在 cmd / PowerShell / Git Bash 中执行

用法：
    python extract_all_revisions.py <源.docx> [输出.docx]

    输出文件省略时，自动写到源文件同目录下，命名为
        <原文件名>_修订内容汇总报告.docx

    不带任何参数直接运行，可查看本说明。
"""

import os
import re
import sys
import copy
import posixpath
import zipfile
import argparse
import datetime
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# Word XML 命名空间
# ---------------------------------------------------------------------------
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WP14_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"
W10_NS = "urn:schemas-microsoft-com:office:word"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPC_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"
ASVG_NS = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
CX_NS = "http://schemas.microsoft.com/office/drawing/2014/chartex"
CS_NS = "http://schemas.openxmlformats.org/drawingml/2006/chartStyle"
DSP_NS = "http://schemas.microsoft.com/office/drawing/2008/diagram"

# 注册前缀，保证用 ElementTree 序列化内嵌图形时输出可读的 w:/a:/pic: 前缀
for _prefix, _uri in (
    ("w", W_NS), ("r", R_NS), ("a", A_NS), ("pic", PIC_NS), ("wp", WP_NS),
    ("wp14", WP14_NS), ("v", V_NS), ("o", O_NS), ("w10", W10_NS),
    ("wps", WPS_NS), ("wpg", WPG_NS), ("wpc", WPC_NS), ("w14", W14_NS),
    ("mc", MC_NS), ("a14", A14_NS), ("asvg", ASVG_NS), ("m", M_NS),
    ("w15", W15_NS), ("c", C_NS), ("dgm", DGM_NS), ("cx", CX_NS),
    ("cs", CS_NS), ("dsp", DSP_NS),
):
    try:
        ET.register_namespace(_prefix, _uri)
    except ValueError:
        pass

R_ID = "{%s}id" % R_NS
R_EMBED = "{%s}embed" % R_NS
R_LINK = "{%s}link" % R_NS
R_PIC = "{%s}pic" % R_NS

DOC_NS_DECL = (
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex" '
    'xmlns:cs="http://schemas.openxmlformats.org/drawingml/2006/chartStyle" '
    'xmlns:dsp="http://schemas.microsoft.com/office/drawing/2008/diagram" '
    'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" '
    'xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
    'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'mc:Ignorable="w14 w15 wp14"'
)

INDENT_STEP = 240
TOC_INDENT_STEP = 260        # 目录条目每级缩进（twips）
TOC_TAB_WIDTH = 8640         # 目录页码右对齐位置（正文宽度，twips）

# 颜色 / 底纹
C_ADD = "1B6B3A"
C_DEL = "9A9A9A"
C_META = "8C8C8C"
FILL_ADD = "E9F5EC"
FILL_DEL = "F3F3F3"
FILL_PARA_ADD = "F7FBF8"
FILL_PARA_DEL = "FAF7F7"
FILL_CELL = "F4F8FB"
FILL_CELL_DEL = "FBF1F1"
BORDER_ADD = "4E9A5E"
BORDER_DEL = "B08A8A"


def q(tag):
    return "{%s}%s" % (W_NS, tag)


CONTENT_TAGS = (q("ins"), q("del"), q("moveTo"), q("moveFrom"))


# ---------------------------------------------------------------------------
# 标题（层级）识别
# ---------------------------------------------------------------------------

TITLE_NAMES = {"title", "标题", "標題"}
HEADING_NAME_RE = re.compile(r"^\s*(?:heading|标题|標題)\s*([1-9])\s*$", re.IGNORECASE)
HEADING_ID_RE = re.compile(r"^\s*heading\s*([1-9])\s*$", re.IGNORECASE)

GUESS_CHAPTER_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇0-9]{1,4}\s*[章篇]")
GUESS_NUM_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,5})[\s、.．]{1,3}(\S.*)$")


def text_of(elem):
    parts = []
    for t in elem.iter(q("t")):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def name_to_level(name):
    """样式名 -> 级别（0 = Title，1..9 = 标题1..9），识别不出返回 None"""
    if not name:
        return None
    n = name.strip()
    if n.lower() in TITLE_NAMES:
        return 0
    m = HEADING_NAME_RE.match(n)
    if m:
        return int(m.group(1))
    return None


def style_level(style_id, styles, _seen=None):
    """把 styleId 解析成标题级别；解析不出返回 None。"""
    if not style_id:
        return None
    if _seen is None:
        _seen = set()
    if style_id in _seen:
        return None
    _seen.add(style_id)

    m = HEADING_ID_RE.match(style_id)
    if m:
        return int(m.group(1))
    if style_id.strip().lower() == "title":
        return 0

    st = styles.get(style_id)
    if st is None:
        if style_id.strip().isdigit():      # WPS 常见的纯数字 styleId
            return int(style_id.strip())
        return None

    lvl = name_to_level(st.get("name"))
    if lvl is None:
        lvl = name_to_level(style_id)
    if lvl is not None:
        return lvl

    if st.get("outline") is not None:
        return st["outline"] + 1

    if st.get("basedOn"):
        return style_level(st["basedOn"], styles, _seen)

    return None


def guess_heading_level(text):
    """没有任何标题样式可用时，用文本形态猜层级。"""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return None
    if t[-1] in "。；，,;":
        return None
    if GUESS_CHAPTER_RE.match(t):
        return 1
    m = GUESS_NUM_RE.match(t)
    if m:
        num, rest = m.group(1), m.group(2)
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", rest):
            return None
        return min(len(num.split(".")), 6)
    return None


def load_styles(z):
    """读取 word/styles.xml，返回 {styleId: {name, outline, basedOn, num}}"""
    try:
        xml_bytes = z.read("word/styles.xml")
    except KeyError:
        return {}
    try:
        sroot = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}

    styles = {}
    for st in sroot.iter(q("style")):
        stype = st.get(q("type"))
        if stype not in (None, "paragraph"):
            continue
        sid = st.get(q("styleId"))
        if not sid:
            continue

        name_el = st.find(q("name"))
        name = name_el.get(q("val")) if name_el is not None else ""

        based_el = st.find(q("basedOn"))
        based = based_el.get(q("val")) if based_el is not None else None

        outline = None
        num = None
        ppr = st.find(q("pPr"))
        if ppr is not None:
            ol = ppr.find(q("outlineLvl"))
            if ol is not None:
                v = (ol.get(q("val")) or "").strip()
                if v.isdigit():
                    outline = int(v)
            num = _read_numpr(ppr)

        styles[sid] = {"name": name, "outline": outline,
                       "basedOn": based, "num": num}
    return styles


def _read_numpr(ppr):
    """从 pPr 里读出 (numId, ilvl)；没有则返回 None"""
    numpr = ppr.find(q("numPr"))
    if numpr is None:
        return None
    nid = numpr.find(q("numId"))
    ilv = numpr.find(q("ilvl"))
    num_id = (nid.get(q("val")) or "").strip() if nid is not None else None
    level = (ilv.get(q("val")) or "").strip() if ilv is not None else None
    if num_id is None and level is None:
        return None
    return (num_id, level)


def _style_num(style_id, styles, _seen=None):
    """沿 basedOn 链查找样式上的编号定义"""
    if not style_id:
        return None
    if _seen is None:
        _seen = set()
    if style_id in _seen:
        return None
    _seen.add(style_id)
    st = styles.get(style_id)
    if st is None:
        return None
    if st.get("num"):
        return st["num"]
    return _style_num(st.get("basedOn"), styles, _seen)


def numbering_of(p, styles):
    """返回段落的 (numId, ilvl)；无编号返回 None。

    优先级：段落直挂 numPr > 段落样式（沿 basedOn 继承）。
    numId == "0" 表示显式取消编号。
    """
    pPr = p.find(q("pPr"))
    style_id = None
    direct = None
    if pPr is not None:
        ps = pPr.find(q("pStyle"))
        if ps is not None:
            style_id = ps.get(q("val"))
        direct = _read_numpr(pPr)

    inherited = _style_num(style_id, styles) if style_id else None

    if direct:
        num_id, level = direct
        if num_id is not None:
            return (num_id, level)
        if level is not None and inherited and inherited[0]:
            return (inherited[0], level)
    if inherited:
        return inherited
    return None


# ---------------------------------------------------------------------------
# 自动编号（numbering.xml）：把 "2.1.1" 这类编号还原成文字
# ---------------------------------------------------------------------------

CHN_DIGITS = "零一二三四五六七八九"
FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")
BULLET_FMTS = ("bullet", "none")


def _read_lvl(lvl):
    """解析一个 w:lvl，返回 {start, fmt, text, lgl, restart}"""
    def val(tag, default=None):
        el = lvl.find(q(tag))
        return el.get(q("val"), default) if el is not None else default

    raw_start = (val("start", "1") or "1").strip()
    try:
        start = int(raw_start)
    except ValueError:
        start = 1

    raw_restart = (val("lvlRestart", "") or "").strip()
    restart = 1
    if raw_restart.isdigit() and int(raw_restart) == 0:
        restart = 0        # lvlRestart=0：上级编号变化时不重置本级

    return {
        "start": start if start > 0 else 1,
        "fmt": (val("numFmt", "decimal") or "decimal").strip(),
        "text": val("lvlText", "") or "",
        "lgl": lvl.find(q("isLgl")) is not None,
        "restart": restart,
    }


def _numbering_style_nums(z):
    """styles.xml 里 type="numbering" 的样式 -> {styleId: numId}"""
    try:
        sroot = ET.fromstring(z.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return {}
    out = {}
    for st in sroot.iter(q("style")):
        if (st.get(q("type")) or "") != "numbering":
            continue
        sid = st.get(q("styleId"))
        ppr = st.find(q("pPr"))
        numpr = ppr.find(q("numPr")) if ppr is not None else None
        nid = numpr.find(q("numId")) if numpr is not None else None
        if sid and nid is not None:
            out[sid] = (nid.get(q("val")) or "").strip()
    return out


def load_numbering(z):
    """读取 word/numbering.xml，返回 {numId: {ilvl: {start, fmt, text, lgl}}}

    会处理 lvlOverride / startOverride，以及 abstractNum 通过 numStyleLink
    间接引用"编号样式"的情况（WPS / 部分模板常见）。
    """
    try:
        data = z.read("word/numbering.xml")
    except KeyError:
        return {}
    try:
        nroot = ET.fromstring(data)
    except ET.ParseError:
        return {}

    abstracts = {}
    for an in nroot.findall(q("abstractNum")):
        aid = an.get(q("abstractNumId"))
        if aid is None:
            continue
        levels = {}
        for lvl in an.findall(q("lvl")):
            ilvl = (lvl.get(q("ilvl")) or "").strip()
            if ilvl.isdigit():
                levels[int(ilvl)] = _read_lvl(lvl)
        link_el = an.find(q("numStyleLink"))
        abstracts[aid] = {
            "levels": levels,
            "link": (link_el.get(q("val")) or "") if link_el is not None else "",
        }

    style_nums = _numbering_style_nums(z)
    raw_nums = {}
    for num in nroot.findall(q("num")):
        nid = num.get(q("numId"))
        if nid is None:
            continue
        aid_el = num.find(q("abstractNumId"))
        raw_nums[nid] = {
            "aid": aid_el.get(q("val")) if aid_el is not None else None,
            "overrides": num.findall(q("lvlOverride")),
        }

    resolved = {}

    def levels_for(aid, seen=None):
        if aid in resolved:
            return resolved[aid]
        seen = seen or set()
        if aid in seen:
            return {}
        seen.add(aid)
        info = abstracts.get(aid) or {"levels": {}, "link": ""}
        levels = dict(info["levels"])
        if not levels and info["link"]:
            # 编号样式 -> 该样式上的 numId -> 它的 abstractNum
            src_num = style_nums.get(info["link"])
            src_aid = raw_nums.get(src_num, {}).get("aid")
            if src_aid:
                levels = dict(levels_for(src_aid, seen))
        resolved[aid] = levels
        return levels

    nums = {}
    aid_count = {}
    for nid, info in raw_nums.items():
        aid_count[info["aid"]] = aid_count.get(info["aid"], 0) + 1
    for nid, info in raw_nums.items():
        levels = dict(levels_for(info["aid"]))
        starts = {}
        for ov in info["overrides"]:
            ilvl = (ov.get(q("ilvl")) or "").strip()
            if not ilvl.isdigit():
                continue
            i = int(ilvl)
            sub = ov.find(q("lvl"))
            if sub is not None:
                lv = _read_lvl(sub)
                levels[i] = lv
                if sub.find(q("start")) is not None:
                    starts[i] = lv["start"]
            elif i not in levels:
                levels[i] = {"start": 1, "fmt": "decimal",
                             "text": "%%%d." % (i + 1), "lgl": False,
                             "restart": 1}
            so = ov.find(q("startOverride"))
            if so is not None:
                v = (so.get(q("val")) or "").strip()
                if v.lstrip("-").isdigit():
                    levels[i] = dict(levels[i])
                    levels[i]["start"] = int(v)
                    starts[i] = int(v)
        if levels:
            # 计数器按 abstractNumId 共享（Word 语义：同一 abstractNum 的多个
            # num 实例共用编号序列，只有 startOverride 才重新开始）
            levels["_aid"] = info["aid"] or ("num:%s" % nid)
            levels["_ov"] = starts
            levels["_shared"] = aid_count.get(info["aid"], 1) > 1
            nums[nid] = levels
    return nums


def _roman(n):
    if n <= 0:
        return str(n)
    table = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
             (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
             (5, "V"), (4, "IV"), (1, "I"))
    out = []
    for value, sym in table:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _letters(n):
    out = []
    while n > 0:
        n, r = divmod(n - 1, 26)
        out.append(chr(65 + r))
    return "".join(reversed(out))


def _chinese_number(n):
    if n <= 0:
        return str(n)
    if n < 10:
        return CHN_DIGITS[n]
    if n < 20:
        return "十" + (CHN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return CHN_DIGITS[n // 10] + "十" + (CHN_DIGITS[n % 10] if n % 10 else "")
    if n < 1000:
        head = CHN_DIGITS[n // 100] + "百"
        rest = n % 100
        if rest == 0:
            return head
        if rest < 10:
            return head + "零" + CHN_DIGITS[rest]
        return head + _chinese_number(rest)
    if n < 10000:
        head = CHN_DIGITS[n // 1000] + "千"
        rest = n % 1000
        if rest == 0:
            return head
        if rest < 100:
            return head + "零" + _chinese_number(rest)
        return head + _chinese_number(rest)
    return str(n)


def format_number(value, fmt, lgl=False):
    """按 numFmt 输出一个编号数字"""
    f = (fmt or "decimal").strip().lower()
    if lgl:
        f = "decimal"
    if f in BULLET_FMTS:
        return ""
    if f in ("decimalzero",):
        return "%02d" % value
    if f in ("decimalfullwidth", "decimalfullwidth2"):
        return str(value).translate(FULLWIDTH_DIGITS)
    if f in ("upperroman",):
        return _roman(value)
    if f in ("lowerroman",):
        return _roman(value).lower()
    if f in ("upperletter",):
        return _letters(value)
    if f in ("lowerletter",):
        return _letters(value).lower()
    if f in ("chinesecounting", "chinesecountingthousand", "japanesecounting",
             "taiwanesecounting", "ideographdigital", "chineselegalsimplified",
             "chineselegaltraditional", "japanesedigitalten", "taiwanesedigital"):
        return _chinese_number(value)
    return str(value)


def _iter_number_paras(root):
    """按文档顺序遍历段落，用于模拟编号计数器。

    跳过 mc:Fallback 子树：同一段内容在 mc:Choice / mc:Fallback 里各存一份
    （文本框常见），两边都数会把编号多加一次。
    """
    fallback = "{%s}Fallback" % MC_NS

    def walk(el):
        for child in el:
            if child.tag == fallback:
                continue
            if child.tag == q("p"):
                yield child
            for sub in walk(child):
                yield sub

    return walk(root)


def _single_level_text(lvl_text, ilvl):
    """lvlText 是否只引用自己这一层（如 ilvl=0 的 "%1."），即单级编号"""
    refs = {int(x) - 1 for x in re.findall(r"%([1-9])", lvl_text or "")}
    return refs == {ilvl}


def compute_numbering_labels(root, numbering, styles):
    """按文档顺序模拟编号计数器，返回 {段落元素: 编号文本}（如 "2.1.1"）"""
    labels = {}
    if not numbering:
        return labels

    counters = {}
    started = set()
    # 独立单级编号列表（标题样式挂 ilvl 0、大纲级别却更深）：Word 在遇到
    # 更浅一级的标题时会重新从 1 开始编号，这里记录该列表的"归属标题级别"
    indep = {}
    dirty = set()
    for p in _iter_number_paras(root):
        # 段落标记被删除：该段在最终稿件里不存在，不占用编号
        if para_mark_state(p) == "del":
            continue
        num = numbering_of(p, styles)
        if not num:
            continue
        num_id, level = num
        num_id = (num_id or "").strip()
        if not num_id or num_id == "0":
            continue
        levels = numbering.get(num_id)
        if not levels:
            continue

        try:
            ilvl = int((level or "0").strip())
        except ValueError:
            ilvl = 0
        cur = levels.get(ilvl) or levels.get(0)
        if not cur:
            continue
        if cur["fmt"].strip().lower() in BULLET_FMTS or not cur["text"]:
            continue

        # 计数器按 abstractNumId 分组：同一 abstractNum 的多个 num 实例共用
        # 一条编号序列（Word 行为），实例自己的 startOverride 才重新开始
        group = levels.get("_aid") or num_id
        ov = levels.get("_ov") or {}

        # 标题级别（仅靠样式判断，不做文字猜测）
        hlvl, _sid = heading_level_of(p, styles)
        if hlvl is not None:
            # 单级编号（只引用自己这一层）且大纲级别比 ilvl 深：独立列表
            if _single_level_text(cur["text"], ilvl) and (hlvl - 1) > ilvl:
                indep[group] = hlvl
            # 出现更浅的标题 -> 这些独立列表重新编号
            for g, own in indep.items():
                if hlvl < own:
                    dirty.add(g)

        cnt = counters.setdefault(group, {})
        if group in dirty:
            # 重新开始：该标题级别及其下级计数清零
            for k in [k for k in cnt if k >= ilvl]:
                cnt.pop(k, None)
            dirty.discard(group)
        # startOverride：人工"重新开始编号 / 设置编号值"在 XML 里就落在
        # numId 的 lvlOverride 上。该 numId 第一次用到这一层时，把计数器
        # 拨回指定值；同一 abstractNum 的其他 numId 不受影响，继续共用序列。
        # 注意不能因为"多个 numId 共用 abstractNum"就忽略它，否则文档中段
        # 人工重启的列表编号会一路累加（Word 显示 1，工具算成 4）。
        if ilvl in ov and (num_id, ilvl) not in started:
            started.add((num_id, ilvl))
            cnt[ilvl] = ov[ilvl] - 1      # startOverride：该实例重新开始
        base = cur["start"] - 1
        cnt[ilvl] = (cnt.get(ilvl) if cnt.get(ilvl) is not None else base) + 1
        # 更深层级重新开始（lvlRestart=0 的层级不重置）
        for deeper in [k for k in cnt if k > ilvl]:
            dlv = levels.get(deeper) or {}
            if dlv.get("restart", 1) == 0:
                continue
            cnt.pop(deeper, None)

        def _sub(m, _cnt=cnt, _levels=levels, _lgl=cur.get("lgl"), _ilvl=ilvl):
            idx = int(m.group(1)) - 1
            lv = _levels.get(idx) or {"start": 1, "fmt": "decimal"}
            v = _cnt.get(idx)
            if v is None:
                v = lv.get("start", 1)
                # 祖先层级还没出现过：Word 会把它落到起始值并保留，
                # 该层级下次出现时从起始值继续。例如一级标题尚未出现就先来了
                # 二级标题 1.1，之后第一个一级标题是 2（不是 1）。
                if idx < _ilvl:
                    _cnt[idx] = v
            return format_number(v, lv.get("fmt", "decimal"), _lgl)

        label = re.sub(r"%([1-9])", _sub, cur["text"]).strip()
        if label:
            labels[p] = label

    return labels


def heading_level_of(p, styles, allow_guess=False):
    """判断段落是否标题，返回 (level, style_id)；level=None 表示不是标题。"""
    style_id = None
    pPr = p.find(q("pPr"))
    if pPr is not None:
        ps = pPr.find(q("pStyle"))
        if ps is not None:
            style_id = ps.get(q("val"))
        ol = pPr.find(q("outlineLvl"))
        if ol is not None:
            v = (ol.get(q("val")) or "").strip()
            if v.isdigit():
                return int(v) + 1, style_id

    lvl = style_level(style_id, styles)
    if lvl is not None:
        return lvl, style_id

    if allow_guess:
        g = guess_heading_level(text_of(p))
        if g is not None:
            return g, style_id

    return None, style_id


def has_styled_heading(root, styles):
    body = root.find(q("body"))
    if body is None:
        return False
    for child in iter_block_children(body):
        if child.tag == q("p"):
            lvl, _s = heading_level_of(child, styles, allow_guess=False)
            if lvl is not None and text_of(child).strip():
                return True
    return False


def style_for_level(level, shift=0):
    """标题级别 -> 报告内使用的 pStyle 名称（shift 用于把原文层级整体下压）"""
    if level is None:
        return "Normal"
    if level <= 0:
        level = 1
    lv = max(1, min(level + shift, 5))
    return "Heading%d" % lv


# ---------------------------------------------------------------------------
# 行内解析：把段落还原成带状态（normal / ins / del）的 run 序列
# ---------------------------------------------------------------------------

def para_mark_state(p):
    """段落标记的修订状态：整段新增 / 整段删除"""
    pPr = p.find(q("pPr"))
    if pPr is None:
        return None
    rPr = pPr.find(q("rPr"))
    if rPr is None:
        return None
    if rPr.find(q("ins")) is not None:
        return "ins"
    if rPr.find(q("del")) is not None:
        return "del"
    return None


OBJ_TAGS = (q("drawing"), q("pict"), q("object"),
            "{%s}AlternateContent" % MC_NS)


def iter_runs(p):
    """按文档顺序返回 [(run_elem, state, link)]

    state: "normal" / "ins" / "del"
    link:  None，或 {"id": 关系ID, "anchor": 书签名}（来自 w:hyperlink）
    """
    out = []

    def walk(el, state, link):
        for c in el:
            if c.tag == q("pPr"):
                continue
            if c.tag in (q("ins"), q("moveTo")):
                walk(c, "ins", link)
                continue
            if c.tag in (q("del"), q("moveFrom")):
                walk(c, "del", link)
                continue
            if c.tag == q("hyperlink"):
                walk(c, state, {"id": c.get(R_ID), "anchor": c.get(q("anchor"))})
                continue
            if c.tag in OBJ_TAGS:
                # 图形对象整体作为一个"run"处理，不深入其内部（避免把文本框里的
                # 段落文字当成正文重复输出）
                out.append((c, state, link))
                continue
            if c.tag == q("r"):
                out.append((c, state, link))
                continue
            walk(c, state, link)

    walk(p, "normal", None)
    return out


def run_objects(r):
    """run（或直接挂在段落里的图形对象）中内嵌的图形对象"""
    if r.tag in OBJ_TAGS:
        return [r]
    return [c for c in r if c.tag in OBJ_TAGS]


def para_has_object(p):
    return any(run_objects(r) for r, _s, _l in iter_runs(p))


def run_text(r):
    d_parts, t_parts = [], []
    for c in r:
        if c.tag == q("rPr"):
            continue
        if c.tag == q("delText"):
            d_parts.append(c.text or "")
        elif c.tag == q("t"):
            t_parts.append(c.text or "")
        elif c.tag == q("tab"):
            (d_parts if d_parts else t_parts).append("\t")
        elif c.tag in (q("br"), q("cr")):
            (d_parts if d_parts else t_parts).append("\n")
        elif c.tag == q("noBreakHyphen"):
            (d_parts if d_parts else t_parts).append("-")
    return "".join(d_parts) if d_parts else "".join(t_parts)


def rpr_dict(r):
    """抽取 run 的字符格式（白名单），用于在报告里还原原文样式"""
    src = r.find(q("rPr"))
    d = {}
    if src is None:
        return d

    rf = src.find(q("rFonts"))
    if rf is not None:
        attrs = []
        for a in ("ascii", "hAnsi", "eastAsia", "cs"):
            v = rf.get(q(a))
            if v:
                attrs.append('w:%s="%s"' % (a, xml_escape(v)))
        if attrs:
            d["rFonts"] = "<w:rFonts %s/>" % " ".join(attrs)

    for tag in ("b", "i", "strike"):
        el = src.find(q(tag))
        if el is not None and el.get(q("val")) not in ("0", "false", "off"):
            d[tag] = True

    for tag in ("color", "sz", "szCs"):
        el = src.find(q(tag))
        v = el.get(q("val")) if el is not None else None
        if v:
            d[tag] = v

    u = src.find(q("u"))
    if u is not None:
        d["u"] = u.get(q("val")) or "single"

    va = src.find(q("vertAlign"))
    if va is not None:
        d["vertAlign"] = va.get(q("val"))

    return d


def emit_rpr(d, state):
    """生成 run 的 rPr；state=ins/del 时叠加标注样式"""
    out = []
    if d.get("rFonts"):
        out.append(d["rFonts"])
    if d.get("b"):
        out.append("<w:b/>")
    if d.get("i"):
        out.append("<w:i/>")
    if d.get("strike") or state == "del":
        out.append("<w:strike/>")

    color = d.get("color")
    if state == "del":
        color = C_DEL
    elif state == "ins" and not color:
        color = C_ADD
    if color:
        out.append('<w:color w:val="%s"/>' % color)

    if d.get("sz"):
        out.append('<w:sz w:val="%s"/>' % d["sz"])
        out.append('<w:szCs w:val="%s"/>' % d["sz"])
    elif d.get("szCs"):
        out.append('<w:szCs w:val="%s"/>' % d["szCs"])
    if d.get("u"):
        out.append('<w:u w:val="%s"/>' % d["u"])

    fill = FILL_ADD if state == "ins" else (FILL_DEL if state == "del" else None)
    if fill:
        out.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill)
    if d.get("vertAlign"):
        out.append('<w:vertAlign w:val="%s"/>' % d["vertAlign"])

    return "<w:rPr>%s</w:rPr>" % "".join(out) if out else ""


def run_content(text):
    out = []
    for ch in str(text):
        if ch == "\t":
            out.append("<w:tab/>")
        elif ch == "\n":
            out.append("<w:br/>")
        else:
            out.append(xml_escape(ch))

    # 合并相邻普通字符，减少 XML 体积
    merged = []
    buf = []
    for piece in out:
        if piece.startswith("&") or len(piece) == 1:
            buf.append(piece)
        else:
            if buf:
                merged.append('<w:t xml:space="preserve">%s</w:t>' % "".join(buf))
                buf = []
            merged.append(piece)
    if buf:
        merged.append('<w:t xml:space="preserve">%s</w:t>' % "".join(buf))
    return "".join(merged)


def make_run(text, state, d):
    return "<w:r>%s%s</w:r>" % (emit_rpr(d, state), run_content(text))


# ---------------------------------------------------------------------------
# 段落 / 表格块级判断
# ---------------------------------------------------------------------------

def para_has_change(p):
    """该段落是否含文字级修订（含整段增删）"""
    if para_mark_state(p):
        return True
    return any(s != "normal" for _r, s, _l in iter_runs(p))


def para_char_count(p):
    """可见内容计量：文字按字符数，内嵌图形对象按 1 计"""
    n = 0
    for r, _s, _l in iter_runs(p):
        if run_objects(r):
            n += 1
        n += len(run_text(r))
    return n


def cell_spans(tr):
    out = []
    for tc in tr.findall(q("tc")):
        tcPr = tc.find(q("tcPr"))
        span = 1
        vm = None
        if tcPr is not None:
            gs = tcPr.find(q("gridSpan"))
            if gs is not None:
                span = int(gs.get(q("val")) or 1)
            v = tcPr.find(q("vMerge"))
            if v is not None:
                vm = v.get(q("val")) or "continue"
        out.append((tc, span, vm))
    return out


def table_rows(tbl):
    """返回 [(tr, state_or_None)]，兼容 w:ins / w:del 包裹的整行"""
    out = []
    for c in tbl:
        if c.tag == q("tr"):
            out.append((c, None))
        elif c.tag in (q("ins"), q("del")):
            for tr in c.findall(q("tr")):
                out.append((tr, "ins" if c.tag == q("ins") else "del"))
    return out


def row_state_of(tr, marker=None):
    trPr = tr.find(q("trPr"))
    if trPr is not None:
        if trPr.find(q("ins")) is not None:
            return "ins"
        if trPr.find(q("del")) is not None:
            return "del"
    return marker


def cell_blocks(tc):
    """单元格内的块级内容（段落 / 嵌套表格），保持原顺序"""
    return [c for c in tc if c.tag in (q("p"), q("tbl"))]


def table_changed(tbl):
    for tr, marker in table_rows(tbl):
        if row_state_of(tr, marker):
            return True
        for tc, _s, _v in cell_spans(tr):
            tcPr = tc.find(q("tcPr"))
            if tcPr is not None and (tcPr.find(q("cellIns")) is not None or
                                     tcPr.find(q("cellDel")) is not None):
                return True
            for b in cell_blocks(tc):
                if b.tag == q("p"):
                    if para_has_change(b):
                        return True
                elif table_changed(b):
                    return True
    return False


def block_changed(b):
    if b["kind"] == "para":
        return para_has_change(b["elem"])
    return table_changed(b["elem"])


# ---------------------------------------------------------------------------
# 记录类表格（版本历史表 / 文档审核记录表）：只列有改动的行
# ---------------------------------------------------------------------------

HISTORY_COLUMN_KEYWORDS = {
    "version": ("版本", "版次", "version", "ver.", "ver "),
    "date": ("日期", "时间", "date", "release"),
    "author": ("作者", "修订人", "修改人", "编制人", "编制", "审核", "批准",
               "责任人", "author", "owner", "modified by"),
    "desc": ("变更", "修订", "修改", "说明", "描述", "内容", "备注",
             "change", "remark", "description", "comment"),
}

HISTORY_SECTION_RE = re.compile(
    r"版本|修订|变更|修改|更新|历史|记录|history|revision|changelog", re.IGNORECASE)

# 审核类表格的列名：按"整格"精确匹配（规范化后全串匹配），
# 因此"审核项目""审核内容""审核要求"这类普通数据列不会被当成审核列名。
REVIEW_CELL_PATTERNS = {
    "person": re.compile(
        r"^(审核|评审|审查|复核|会签|签署|审批|批准|校对|校核|编制|制定|审定)"
        r"(人|人员|员|者|人签字|签字|签名)?$"
        r"|^(姓名|签字|签名|责任人|审核者)$"
        r"|^(reviewer|approver|auditor|reviewedby|approvedby|signedby|signature)$",
        re.IGNORECASE),
    "opinion": re.compile(
        r"^(审核|评审|审查|复核|会签|审批|批准|处理|整改)?"
        r"(意见|结论|评语|建议|备注|说明|结果)$"
        r"|^(opinion|comment|comments|conclusion|result|remark|remarks)$",
        re.IGNORECASE),
    "date": re.compile(
        r"^(审核|评审|审查|会签|签署|审批|批准|编制|填报|完成)?(日期|时间|日期时间)$"
        r"|^(date|time)$",
        re.IGNORECASE),
    "stage": re.compile(
        r"^(审核|评审|审查|会签|审批)?(环节|阶段|步骤|节点|轮次|流程)$"
        r"|^(stage|step|phase)$",
        re.IGNORECASE),
    "dept": re.compile(
        r"^(审核|评审|审查|会签|审批)?(部门|单位|岗位|角色|职务|专业)$"
        r"|^(department|dept|role|position)$",
        re.IGNORECASE),
}

REVIEW_TITLE_RE = re.compile(
    r"审核记录|评审记录|审查记录|会签记录|审批记录|签署记录|校审记录|审核表|评审表|"
    r"审查表|会签表|审批表|签署表|审核意见|评审意见|reviewrecord|approvalrecord|sign-?off",
    re.IGNORECASE)

REVIEW_SECTION_RE = re.compile(
    r"审核|评审|审查|复核|会签|签署|审批|批准|校对|校审|review|approv|sign",
    re.IGNORECASE)

# "文档审核记录"这类模块：整个模块只保留改动内容（不保留未改动的表格行 / 段落）
AUDIT_MODULE_RE = re.compile(
    r"审核记录|评审记录|审查记录|会签记录|签署记录|校审记录|审批记录|"
    r"文档审核|审核表|评审表|审查表|会签表|签署表|审批表|审核栏|签署栏|"
    r"审核意见|评审意见|reviewrecord|approvalrecord",
    re.IGNORECASE)


def is_audit_module_title(text, allow_long=False):
    """标题/小标题是否是"文档审核记录"这类模块

    正文里的普通段落只认短标题：避免"以上为本次文档的全部审核记录，
    纸质件由质量部归档。"这种句子被误当成模块标题。
    """
    raw = (text or "").strip()
    if not raw or (not allow_long and len(raw) > 20):
        return False
    if re.search(r"[。！？；]", raw):
        return False
    t = _norm_cell(raw)
    return bool(t) and bool(AUDIT_MODULE_RE.search(t))


def _row_text(tr):
    return " ".join(text_of(tc) for tc in tr.findall(q("tc"))).lower()


def _history_hits(text):
    return {k: any(kw in text for kw in kws)
            for k, kws in HISTORY_COLUMN_KEYWORDS.items()}


def is_version_history_table(tbl, heading_text=""):
    """判断是否为"文档版本历史 / 修订记录"类表格。

    判定依据：首行（含所在小节标题）必须出现"版本"类列名，
    且列名命中数 >= 4；或 >= 3 且所在小节标题带"版本/修订/变更/记录"等字样。
    这样 `接口版本 | 说明 | 日期` 这类普通参数表不会被误判。
    """
    rows = [tr for tr, _m in table_rows(tbl)]
    if len(rows) < 2:
        return False

    # 前两行一起看：有些表格第一行是合并的"修订记录"标题行
    head = " ".join(_row_text(tr) for tr in rows[:2])
    hits = _history_hits(head + " " + (heading_text or "").lower())
    score = sum(1 for v in hits.values() if v)
    if not hits["version"]:
        return False
    if score >= 4:
        return True
    return score >= 3 and bool(HISTORY_SECTION_RE.search(heading_text or ""))


def _norm_cell(text):
    """单元格文字规范化：去掉空白与常见分隔符，便于整格比对"""
    return re.sub(r"[\s\u3000:：()（）\[\]【】/\\\-—_、.。]+", "", text or "").lower()


def _cell_roles(text):
    t = _norm_cell(text)
    if not t or len(t) > 12:
        return set()
    return {role for role, rx in REVIEW_CELL_PATTERNS.items() if rx.match(t)}


def _table_roles(tbl, max_rows=None):
    """表格里出现过的审核列名角色。

    扫描范围不限于前两行：`审核项 | 审核情况` 这种竖向表单，
    角色名在左侧行首，下面几行才是"审核人 / 审核日期 / 审核意见"。
    """
    rows = list(table_rows(tbl))
    if max_rows is not None:
        rows = rows[:max_rows]
    roles = set()
    for tr, _m in rows:
        for tc, _s, _v in cell_spans(tr):
            roles |= _cell_roles(text_of(tc))
    return roles


AUDIT_VERB_RE = re.compile(r"审核|评审|审查|复核|会签|签署|审批|批准|校对|校审")


def _is_vertical_audit_form(tbl):
    """竖向审核表单：左侧一列是"审核人 / 审核日期 / 审核意见"这类标签

    要求 >= 2 行带"审核/评审/会签/签署"等动作字样、且角色类别 >= 2 类，
    否则 `姓名 | 张三`、`部门 | 技术部` 这种普通信息表会被误判。
    """
    n, kinds = 0, set()
    for tr, _m in table_rows(tbl):
        cells = tr.findall(q("tc"))
        if not cells:
            continue
        txt = text_of(cells[0])
        if _cell_roles(txt) and AUDIT_VERB_RE.search(txt):
            n += 1
            kinds |= _cell_roles(txt)
    return n >= 2 and len(kinds) >= 2


def _is_review_sign_block(tbl):
    """签署块：编制 / 审核 / 批准 这类角色格。

    纵向：角色名各占一行（首列连续 >= 2 行是角色名）；
    横向：首行各列都是角色名，下面 1~2 行是签署人与日期。
    """
    rows = [tr for tr, _m in table_rows(tbl)]
    if not rows:
        return False

    n = 0
    for tr in rows:
        cells = tr.findall(q("tc"))
        if cells and "person" in _cell_roles(text_of(cells[0])):
            n += 1
    if n >= 2:
        return True

    if len(rows) <= 4:
        head_cells = rows[0].findall(q("tc"))
        roles = [_cell_roles(text_of(tc)) for tc in head_cells]
        if len(roles) >= 2 and all("person" in r for r in roles):
            return True
    return False


def is_review_record_table(tbl, heading_text=""):
    """判断是否为"文档审核记录 / 评审 / 会签 / 签署"类表格。

    判定依据（任一成立）：
    1) 表名（前两行文字）或所在小节标题出现"审核记录表 / 评审记录 / 会签记录"等字样，
       且表格至少有一列是审核角色 / 意见 / 日期 / 环节 / 部门；
    2) 列名命中 >= 3 类审核列（人、意见、日期、环节、部门），且含人/环节/部门；
    3) 列名命中 >= 2 类（含人/环节/部门），且标题带"审核 / 评审 / 会签"等字样；
    4) "编制 / 审核 / 批准"签署块（首列 >= 2 行是角色名）且标题带审核字样。

    列名按整格精确匹配，所以 `序号 | 审核项目 | 审核内容 | 日期` 这类
    普通内容表不会被误判成审核记录表。
    """
    rows = [tr for tr, _m in table_rows(tbl)]
    if len(rows) < 2:
        return False
    roles = _table_roles(tbl)
    if not roles:
        return False

    # 竖向表单：左侧一列连续是"审核人 / 审核日期 / 审核意见"这类标签
    if _is_vertical_audit_form(tbl):
        return True

    head = " ".join(_row_text(tr) for tr in rows[:2])
    blob = head + " " + (heading_text or "")
    if REVIEW_TITLE_RE.search(_norm_cell(blob)):
        return True

    if roles & {"person", "stage", "dept"}:
        if len(roles) >= 3:
            return True
        if len(roles) >= 2 and (REVIEW_SECTION_RE.search(heading_text or "") or
                                REVIEW_SECTION_RE.search(head)):
            return True
        return _is_review_sign_block(tbl) and bool(REVIEW_SECTION_RE.search(blob))
    return False


def row_changed(tr, marker=None):
    """该行是否包含任意修订（整行增删、单元格增删、单元格内文字改动）"""
    if row_state_of(tr, marker):
        return True
    for tc, _span, _vm in cell_spans(tr):
        tcPr = tc.find(q("tcPr"))
        if tcPr is not None and (tcPr.find(q("cellIns")) is not None or
                                 tcPr.find(q("cellDel")) is not None):
            return True
        for b in cell_blocks(tc):
            if b.tag == q("p"):
                if para_has_change(b):
                    return True
            elif table_changed(b):
                return True
    return False


def _review_role_cells(tr):
    return sum(1 for tc, _s, _v in cell_spans(tr) if _cell_roles(text_of(tc)))


def _row_is_named(tr, kind):
    """该行是否像表头行（带列名）"""
    if kind == "history":
        txt = _row_text(tr)
        return any(kw in txt for kws in HISTORY_COLUMN_KEYWORDS.values()
                   for kw in kws)
    # 审核表：至少两个格子是列名，才算表头；签署块每行只有 1 个角色格，不算表头
    return _review_role_cells(tr) >= 2


def _row_is_title(tr):
    """整行合并成一格的表名行，如"文档审核记录表"

    也兼容"表名 + 若干空单元格"这种没设 gridSpan 的伪合并行。
    """
    cells = tr.findall(q("tc"))
    if not cells:
        return False
    txt = _norm_cell(text_of(tr))
    if not txt or not REVIEW_TITLE_RE.search(txt):
        return False
    return len(cells) == 1 or all(not text_of(c).strip() for c in cells[1:])


def _row_is_vertical_header(tr, rows, idx):
    """竖向表单的表头行，如 `审核项 | 审核情况`（第一行不含角色名）

    判据：两列，且紧随其后的行里有 >= 2 行首格是角色名。
    """
    if len(tr.findall(q("tc"))) != 2:
        return False
    n = 0
    for tr2, _m in rows[idx + 1: idx + 8]:
        cells = tr2.findall(q("tc"))
        if not cells:
            continue
        txt = text_of(cells[0])
        if _cell_roles(txt) and AUDIT_VERB_RE.search(txt):
            n += 1
    return n >= 2


def record_rows(tbl, heading_text="", force_kind=None):
    """记录类表格 -> (保留的行, 省略的行数, 类型, 有改动的行数)

    非记录类表格返回 (None, 0, None, 0)。
    保留：表头行（前 1~3 行里连续的、未改动且带列名的行）+ 所有有改动的行。
    签署块没有表头行，只保留有改动的行。
    force_kind="review" 时无条件按审核记录表处理（用于"文档审核记录"整个模块）。
    """
    if force_kind:
        kind = force_kind
    elif is_version_history_table(tbl, heading_text):
        kind = "history"
    elif is_review_record_table(tbl, heading_text):
        kind = "review"
    else:
        return None, 0, None, 0

    rows = list(table_rows(tbl))
    head_n = 0
    for i, (tr, marker) in enumerate(rows[:3]):
        if row_changed(tr, marker):
            break
        if i == 0:
            if (kind == "history" or _row_is_title(tr) or _row_is_named(tr, kind)
                    or (kind == "review" and _row_is_vertical_header(tr, rows, 0))):
                head_n = 1
            else:
                break           # 第一行就不是表头（如签署块），不保留
        elif _row_is_named(tr, kind):
            head_n = i + 1
        else:
            break

    if kind == "history":
        head_n = max(head_n, 1)     # 版本表至少要保留第一行表头
    head = rows[:head_n]
    body = rows[head_n:]
    changed = [(tr, m) for tr, m in body if row_changed(tr, m)]
    return head + changed, len(body) - len(changed), kind, len(changed)


# ---------------------------------------------------------------------------
# 标题树：只输出有改动的路径，但每个节点保留自己的完整内容
# ---------------------------------------------------------------------------

def iter_block_children(parent):
    """按顺序产出块级子元素，展开 w:sdt/w:sdtContent

    Word / WPS 生成的目录、"封面"等内容控件都包在 w:sdt 里，
    不展开的话整块内容（包括目录条目）会被漏掉。
    """
    for child in parent:
        if child.tag == q("sdt"):
            content = child.find(q("sdtContent"))
            if content is not None:
                for c in iter_block_children(content):
                    yield c
        elif child.tag in (q("p"), q("tbl")):
            yield child


def build_tree(root, styles, allow_guess, labels=None):
    body = root.find(q("body"))
    tree = {"level": 0, "text": "", "children": [], "blocks": []}
    stack = [tree]

    for child in (list(iter_block_children(body)) if body is not None else []):
        if child.tag == q("p"):
            lvl, _sid = heading_level_of(child, styles, allow_guess=allow_guess)
            if lvl is not None:
                raw = text_of(child).strip()
                label = (labels or {}).get(child, "")
                # 自动编号的标题：把编号补回标题文字（原文若已手打编号则不重复）
                text = raw if (not label or raw.startswith(label)) else (label + " " + raw).strip()
                if lvl == 0 or not text:
                    # Title / 空标题：不当标题节点，但内容要保留
                    stack[-1]["blocks"].append({"kind": "para", "elem": child})
                    continue
                while len(stack) > 1 and stack[-1]["level"] >= lvl:
                    stack.pop()
                node = {"level": lvl, "text": text, "children": [], "blocks": [],
                        "elem": child, "parent": stack[-1]}
                stack[-1]["children"].append(node)
                stack.append(node)
                continue
            stack[-1]["blocks"].append({"kind": "para", "elem": child})
        elif child.tag == q("tbl"):
            stack[-1]["blocks"].append({"kind": "table", "elem": child})

    return tree


def node_changed(node):
    if node.get("elem") is not None and para_has_change(node["elem"]):
        return True
    if any(block_changed(b) for b in node["blocks"]):
        return True
    return any(node_changed(c) for c in node["children"])


# ---------------------------------------------------------------------------
# 目录（TOC）：只保留"目标小节有改动"的条目
# ---------------------------------------------------------------------------

TOC_STYLE_RE = re.compile(r"^(?:toc|目录)[\s_\-]*(\d)?$", re.IGNORECASE)
TOC_HYPERLINK_RE = re.compile(r'HYPERLINK\s+\\l\s+"?([^"\s\\]+)"?', re.IGNORECASE)
TOC_INSTR_RE = re.compile(r"^\s*TOC\b", re.IGNORECASE)


def _toc_style_level(label):
    m = TOC_STYLE_RE.match((label or "").strip())
    if not m:
        return None
    return int(m.group(1) or 1)


def toc_level_of(p, styles):
    """段落是否是目录条目：是则返回条目级别（1~9），否则 None

    兼容 Word 的 TOC1~TOC9、样式名 "toc 1"、中文 "目录 1"，
    并沿 basedOn 链继承；"TOC Heading"（目录标题）不算条目。
    """
    pPr = p.find(q("pPr"))
    if pPr is None:
        return None
    ps = pPr.find(q("pStyle"))
    if ps is None:
        return None

    cur = ps.get(q("val"))
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        st = styles.get(cur) or {}
        lv = _toc_style_level(cur)
        if lv is None:
            lv = _toc_style_level(st.get("name"))
        if lv is not None:
            return lv
        cur = st.get("basedOn")
    return None


def toc_anchor(p):
    """目录条目指向的书签名：优先 w:hyperlink w:anchor，其次 HYPERLINK 域"""
    for h in p.iter(q("hyperlink")):
        a = h.get(q("anchor"))
        if a:
            return a
    for it in p.iter(q("instrText")):
        m = TOC_HYPERLINK_RE.search(it.text or "")
        if m:
            return m.group(1)
    return None


def bookmark_names(p):
    out = set()
    for b in p.iter(q("bookmarkStart")):
        n = b.get(q("name"))
        if n:
            out.add(n)
    return out


def _norm_toc_text(t):
    t = re.sub(r"[\s\u3000]+", "", t or "")
    t = re.sub(r"[.·…\-—_]{2,}$", "", t)     # 尾部引导点
    return t


def _strip_leading_number(t):
    return re.sub(r"^[0-9]+(?:\.[0-9]+)*[、.．)）]?", "", t)


def _match_toc_text(p, by_text):
    """目录条目没有可用书签时，按标题文字前缀匹配（条目末尾通常带页码）"""
    t = _norm_toc_text(text_of(p))
    if not t:
        return None
    cands = [t, _strip_leading_number(t)]
    best = None
    for cand in cands:
        for ht, ch in by_text.items():
            if ht and cand.startswith(ht):
                if best is None or len(ht) > best[0]:
                    best = (len(ht), ch)
    return best[1] if best else None


def annotate_toc(tree, styles):
    """给目录条目打标记 -> (info, groups)

    info:  {id(段落): {"keep": bool, "group": 组号, "level": 级别}}
    groups:{组号: {"kept": 保留条数, "omitted": 省略条数}}

    "有改动"指该标题自身、正文/表格、或任意子节有修订（node_changed）。
    找不到对应标题的条目一律保留（多半是"整节被删除"类条目，不能丢）。
    """
    by_name, by_text = {}, {}

    def walk(node):
        el = node.get("elem")
        if el is not None:
            ch = node_changed(node)
            for n in bookmark_names(el):
                by_name[n] = ch
            t = _norm_toc_text(node.get("text") or text_of(el))
            if t:
                by_text.setdefault(t, ch)
        for c in node["children"]:
            walk(c)

    walk(tree)

    info, groups = {}, {}
    gid = 0

    def scan(node):
        nonlocal gid
        cur = None
        self_bm = bookmark_names(node["elem"]) if node.get("elem") is not None else set()
        for b in node["blocks"]:
            p = b["elem"] if b["kind"] == "para" else None
            lvl = toc_level_of(p, styles) if p is not None else None
            if lvl is None:
                cur = None
                continue

            if cur is None:
                gid += 1
                cur = gid
                groups[cur] = {"kept": 0, "omitted": 0}

            anchor = toc_anchor(p)
            if anchor and anchor in self_bm:
                # 目录条目指向"目录"本身（有些模板会列自己），没有意义
                keep = False
            elif anchor and anchor in by_name:
                keep = by_name[anchor]
            else:
                changed = _match_toc_text(p, by_text)
                keep = True if changed is None else changed

            groups[cur]["kept" if keep else "omitted"] += 1
            info[id(p)] = {"keep": keep, "group": cur, "level": lvl}

        for c in node["children"]:
            scan(c)

    scan(tree)
    return info, groups




def _classify_para(p, st):
    pstate = para_mark_state(p)
    has_ins = has_del = False
    for r, state, _link in iter_runs(p):
        if pstate and state == "normal":
            state = pstate
        if not run_text(r).strip() and not run_objects(r):
            continue
        if state == "ins":
            has_ins = True
        elif state == "del":
            has_del = True
    if has_ins and has_del:
        st["mod"] += 1
    elif has_ins:
        st["add"] += 1
    elif has_del:
        st["del"] += 1


def _classify_table(tbl, st):
    for tr, marker in table_rows(tbl):
        rs = row_state_of(tr, marker)
        if rs == "ins":
            st["add"] += 1
            continue
        if rs == "del":
            st["del"] += 1
            continue
        for tc, _s, _v in cell_spans(tr):
            sub = {"para": 0, "table": 0, "add": 0, "del": 0, "mod": 0}
            tcPr = tc.find(q("tcPr"))
            if tcPr is not None and tcPr.find(q("cellIns")) is not None:
                st["add"] += 1
                continue
            if tcPr is not None and tcPr.find(q("cellDel")) is not None:
                st["del"] += 1
                continue
            for b in cell_blocks(tc):
                if b.tag == q("p"):
                    if para_has_change(b):
                        _classify_para(b, sub)
                elif table_changed(b):
                    _classify_table(b, sub)
            for k in ("add", "del", "mod"):
                st[k] += sub[k]


def count_node(node):
    """统计节点及其子树的：正文段落数、表格数、新增/删除/修改条目数"""
    st = {"para": 0, "table": 0, "add": 0, "del": 0, "mod": 0}
    # 标题自身的改动计入 add/del/mod，但不计入"正文段落数"
    if node.get("elem") is not None and para_has_change(node["elem"]):
        _classify_para(node["elem"], st)
    for b in node["blocks"]:
        if b["kind"] == "para":
            if para_has_change(b["elem"]):
                st["para"] += 1
                _classify_para(b["elem"], st)
        else:
            if block_changed(b):
                st["table"] += 1
                _classify_table(b["elem"], st)
    for c in node["children"]:
        sub = count_node(c)
        for k in st:
            st[k] += sub[k]
    return st


def count_marks(root):
    return sum(1 for el in root.iter() if el.tag in CONTENT_TAGS)


def count_style_revs(root):
    return sum(1 for el in root.iter() if el.tag in (q("rPrChange"), q("pPrChange")))


def top_chapters(node):
    return sum(1 for c in node["children"] if node_changed(c))


def changed_heading_count(node):
    n = 0
    for c in node["children"]:
        if node_changed(c):
            n += 1
        n += changed_heading_count(c)
    return n


# ---------------------------------------------------------------------------
# 关系（relationships）与内嵌部件：图片 / 图表 / OLE / 超链接
# ---------------------------------------------------------------------------

# 源文档里没写 content-type 时的兜底
EXT_FALLBACK = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "jpe": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff", "ico": "image/x-icon",
    "emf": "image/x-emf", "wmf": "image/x-wmf", "svg": "image/svg+xml",
    "webp": "image/webp",
    "bin": "application/vnd.openxmlformats-officedocument.oleObject",
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "xml": "application/xml",
}


def _resolve_part(base_dir, target):
    """把关系里的相对 Target 解析成包内部件路径"""
    t = (target or "").replace("\\", "/").strip()
    if not t:
        return ""
    if t.startswith("/"):
        return t.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, t))


def _rel_id(elem):
    for attr in (R_EMBED, R_ID, R_LINK):
        v = elem.get(attr)
        if v:
            return v
    return None


def load_doc_rels(z):
    """读取 word/_rels/document.xml.rels"""
    try:
        data = z.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out = {}
    for rel in root:
        rid = rel.get("Id")
        if not rid:
            continue
        out[rid] = {
            "type": rel.get("Type") or "",
            "target": rel.get("Target") or "",
            "mode": rel.get("TargetMode") or None,
        }
    return out


def load_content_types(z):
    """读取源包的 [Content_Types].xml，返回 (defaults, overrides)"""
    try:
        root = ET.fromstring(z.read("[Content_Types].xml"))
    except (KeyError, ET.ParseError):
        return {}, {}
    defaults, overrides = {}, {}
    for el in root:
        tag = el.tag.split("}")[-1]
        if tag == "Default":
            ext = (el.get("Extension") or "").lower()
            ct = el.get("ContentType") or ""
            if ext and ct:
                defaults[ext] = ct
        elif tag == "Override":
            part = (el.get("PartName") or "").lstrip("/")
            ct = el.get("ContentType") or ""
            if part and ct:
                overrides[part] = ct
    return defaults, overrides


def make_ctx(z, styles, labels=None):
    defaults, overrides = load_content_types(z)
    return {
        "z": z,
        "styles": styles,
        "labels": labels or {},
        "rels": load_doc_rels(z),
        "rid_map": {},
        "out_rels": [],
        "parts": {},            # 输出部件路径 -> 源部件路径
        "extra_parts": set(),   # 部件自带的 .rels 等
        "exts": set(),          # 需要写 Default 的扩展名
        "overrides": {},        # 输出部件需要的 Override
        "ct_defaults": defaults,
        "ct_overrides": overrides,
        "has_numbering": "word/numbering.xml" in z.namelist(),
        "numbering": False,
    }


def collect_part(ctx, part, seen=None):
    """把源包中的一个部件（及其 .rels 指向的子部件）登记为待复制"""
    if not part:
        return
    if seen is None:
        seen = set()
    if part in seen:
        return
    seen.add(part)
    ctx["parts"][part] = part

    ext = posixpath.splitext(part)[1].lstrip(".").lower()
    if ext:
        ctx["exts"].add(ext)
    if part in ctx["ct_overrides"]:
        ctx["overrides"][part] = ctx["ct_overrides"][part]

    # 部件自身的 .rels（图表、嵌入对象等还有下一层引用）
    rels_part = posixpath.join(posixpath.dirname(part), "_rels",
                               posixpath.basename(part) + ".rels")
    z = ctx["z"]
    try:
        data = z.read(rels_part)
    except KeyError:
        return
    ctx["extra_parts"].add(rels_part)
    ctx["exts"].add("rels")
    try:
        rroot = ET.fromstring(data)
    except ET.ParseError:
        return
    base_dir = posixpath.dirname(part)
    for rel in rroot:
        if (rel.get("TargetMode") or "") == "External":
            continue
        target = _resolve_part(base_dir, rel.get("Target"))
        collect_part(ctx, target, seen)


def remap_rid(ctx, old_id):
    """把源文档里的关系 ID 换成本报告里的新 ID，并登记待复制部件"""
    if not old_id:
        return None
    if old_id in ctx["rid_map"]:
        return ctx["rid_map"][old_id]

    rel = ctx["rels"].get(old_id)
    if rel is None:
        ctx["rid_map"][old_id] = None
        return None

    new_id = "rIdR%d" % (len(ctx["out_rels"]) + 1)
    ctx["rid_map"][old_id] = new_id

    if rel["mode"] == "External":
        ctx["out_rels"].append({
            "id": new_id, "type": rel["type"],
            "target": rel["target"], "mode": "External",
        })
        return new_id

    part = _resolve_part("word", rel["target"])
    collect_part(ctx, part)
    ctx["out_rels"].append({
        "id": new_id, "type": rel["type"],
        "target": rel["target"], "mode": None,
    })
    return new_id


def _anchor_to_inline(node):
    """浮动图片转成嵌入式，避免在报告中互相重叠"""
    for el in node.iter():
        if el.tag == "{%s}anchor" % WP_NS:
            el.tag = "{%s}inline" % WP_NS
            for attr in ("simplePos", "relativeHeight", "behindDoc", "locked",
                         "layoutInCell", "allowOverlap", "hidden"):
                el.attrib.pop(attr, None)
            for ch in list(el):
                tag = ch.tag
                if (tag in ("{%s}simplePos" % WP_NS,
                            "{%s}positionH" % WP_NS,
                            "{%s}positionV" % WP_NS)
                        or tag.startswith("{%s}wrap" % WP_NS)):
                    el.remove(ch)


def render_object(ctx, el):
    """把 w:drawing / w:pict / w:object 原样搬到报告，只重映射关系 ID"""
    node = copy.deepcopy(el)
    _anchor_to_inline(node)

    for sub in node.iter():
        for attr in (R_EMBED, R_ID, R_LINK):
            if attr in sub.attrib:
                new_id = remap_rid(ctx, sub.attrib[attr])
                if new_id:
                    sub.attrib[attr] = new_id
                else:
                    sub.attrib.pop(attr, None)

    return ET.tostring(node, encoding="unicode")


# ---------------------------------------------------------------------------
# 文档构造辅助
# ---------------------------------------------------------------------------

def _runs_from_text(text):
    lines = str(text).split("\n")
    out = []
    for i, line in enumerate(lines):
        if i:
            out.append("<w:br/>")
        if line:
            out.append('<w:t xml:space="preserve">%s</w:t>' % xml_escape(line))
    return "".join(out)


def _ppr(style=None, center=False, indent=None, shade=None):
    """按 OOXML 规定顺序拼 pPr：pStyle -> shd -> ind -> jc"""
    parts = []
    if style:
        parts.append('<w:pStyle w:val="%s"/>' % xml_escape(style))
    if shade:
        parts.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade)
    if indent:
        parts.append('<w:ind w:left="%d"/>' % int(indent))
    if center:
        parts.append('<w:jc w:val="center"/>')
    return "<w:pPr>%s</w:pPr>" % "".join(parts) if parts else ""


def xml_para(text, *, style=None, bold=False, center=False, size=None,
             indent=None, shade=None):
    rpr_parts = []
    if bold:
        rpr_parts.append("<w:b/>")
    if size:
        rpr_parts.append('<w:sz w:val="%s"/><w:szCs w:val="%s"/>' % (size, size))
    rpr = "<w:rPr>%s</w:rPr>" % "".join(rpr_parts) if rpr_parts else ""
    return "<w:p>%s<w:r>%s%s</w:r></w:p>" % (
        _ppr(style=style, center=center, indent=indent, shade=shade),
        rpr, _runs_from_text(text))


def xml_field_para(label, value):
    label_run = ('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                 % xml_escape(label))
    val_run = '<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % xml_escape(value)
    return "<w:p>%s%s</w:p>" % (label_run, val_run)


def xml_table(rows, *, col_widths=None, header_fill="4472C4", alt_fill="D9E1F2",
              tbl_ind=0):
    if not rows:
        return ""
    n = len(rows[0])
    if col_widths is None:
        col_widths = [int(9000 / n)] * n

    grid_cols = "".join('<w:gridCol w:w="%s"/>' % w for w in col_widths)
    tbl_pr = (
        '<w:tblPr>'
        '<w:tblStyle w:val="TableGrid"/>'
        '<w:tblW w:type="auto" w:w="0"/>'
        '<w:tblInd w:type="dxa" w:w="%d"/>' % int(tbl_ind) +
        '<w:tblLayout w:type="fixed"/>'
        '<w:tblLook w:firstColumn="1" w:firstRow="1" w:lastColumn="0" w:lastRow="0" '
        'w:noHBand="0" w:noVBand="1" w:val="04A0"/>'
        '</w:tblPr>'
    )
    tbl_grid = "<w:tblGrid>%s</w:tblGrid>" % grid_cols

    rows_xml = []
    for ri, row in enumerate(rows):
        cells_xml = []
        for ci, cell in enumerate(row):
            w = col_widths[ci] if ci < len(col_widths) else col_widths[-1]
            if ri == 0 and header_fill:
                shd = '<w:shd w:fill="%s"/>' % header_fill
            elif alt_fill:
                shd = '<w:shd w:fill="%s"/>' % alt_fill
            else:
                shd = ""
            tc_pr = '<w:tcPr><w:tcW w:type="dxa" w:w="%s"/>%s</w:tcPr>' % (w, shd)
            cells_xml.append("<w:tc>%s%s</w:tc>" % (tc_pr, xml_para(str(cell))))
        rows_xml.append("<w:tr>%s</w:tr>" % "".join(cells_xml))

    return "<w:tbl>%s%s%s</w:tbl>" % (tbl_pr, tbl_grid, "".join(rows_xml))


def spacer():
    return "<w:p/>"


def meta_line(text, indent=120):
    return ('<w:p><w:pPr><w:spacing w:before="0" w:after="120"/>'
            '<w:ind w:left="%d"/></w:pPr>'
            '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="18"/><w:szCs w:val="18"/>'
            '</w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>') % (
        int(indent), C_META, xml_escape(text))


# ---------------------------------------------------------------------------
# 渲染：整节内容 + 就地标注
# ---------------------------------------------------------------------------

def render_para(p, *, ctx, indent=0, style=None, mark_para=True, compact=False,
                force_state=None, tabs=None, plain=False, quote=False):
    """plain=True 用于输出"被参考章节原文"：不标注修订，删除内容不输出

    quote=True 时整段加浅灰左边线，表示这是照录的引用块。
    """
    if plain and para_mark_state(p) == "del":
        return ""
    pstate = None if plain else (para_mark_state(p) or force_state)
    pieces = []
    seen_ins = seen_del = False

    cur_link_key = (None, None)
    link_open = False

    def close_link():
        nonlocal link_open
        if link_open:
            pieces.append("</w:hyperlink>")
            link_open = False

    for r, state, link in iter_runs(p):
        if plain:
            if state == "del":
                continue
            state = "normal"
        elif pstate and state == "normal":
            state = pstate

        objs = run_objects(r)
        txt = run_text(r)
        if not txt and not objs:
            continue
        if state == "ins":
            seen_ins = True
        elif state == "del":
            seen_del = True

        # 超链接：连续的同一链接 run 包在一个 w:hyperlink 里
        link_key = (link.get("id"), link.get("anchor")) if link else (None, None)
        if link_key != cur_link_key:
            close_link()
            if link:
                attrs = []
                rid = remap_rid(ctx, link.get("id")) if link.get("id") else None
                if rid:
                    attrs.append('r:id="%s"' % rid)
                anchor = link.get("anchor")
                if anchor:
                    attrs.append('w:anchor="%s"' % xml_escape(anchor))
                if attrs:
                    pieces.append("<w:hyperlink %s>" % " ".join(attrs))
                    link_open = True
            cur_link_key = link_key

        if txt:
            pieces.append(make_run(txt, state, rpr_dict(r)))
        if objs:
            if state != "normal":
                pieces.append(make_run(
                    "〔新增图片〕" if state == "ins" else "〔已删除图片〕", state, {}))
            for el in objs:
                pieces.append("<w:r>%s%s</w:r>" % (
                    emit_rpr(rpr_dict(r), state), render_object(ctx, el)))
    close_link()

    # 标题的自动编号：报告里改用手写编号文字，避免与报告自身章节号冲突
    if style is not None:
        label = (ctx.get("labels") or {}).get(p)
        # 原文标题若已手打编号（如 "1.1 范围"），不再重复添加
        if label and not text_of(p).strip().startswith(label):
            pieces.insert(0, make_run(label + " ", "normal", {}))

    changed = seen_ins or seen_del

    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % xml_escape(style))

    # 自动编号：段落直挂或从样式继承（源文档没有 numbering.xml 时不写引用）
    # style 非空表示这是报告里的标题段落，不参与原文档的自动编号，
    # 否则会出现"1 第一章 xxx"这类与报告章节号冲突的编号
    numbered = False
    num = numbering_of(p, ctx["styles"]) if style is None else None
    if num and num[0] and num[0] != "0" and ctx.get("has_numbering"):
        ctx["numbering"] = True
        numbered = True
        ppr.append('<w:numPr><w:ilvl w:val="%s"/><w:numId w:val="%s"/></w:numPr>'
                   % (xml_escape(num[1] or "0"), xml_escape(num[0])))

    if changed and mark_para and not compact:
        if seen_del and not seen_ins:
            ppr.append('<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" '
                       'w:color="%s"/></w:pBdr>' % BORDER_DEL)
            ppr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
                       % FILL_PARA_DEL)
        else:
            ppr.append('<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" '
                       'w:color="%s"/></w:pBdr>' % BORDER_ADD)
            ppr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
                       % FILL_PARA_ADD)
    if quote and not (changed and mark_para and not compact):
        ppr.append('<w:pBdr><w:left w:val="single" w:sz="12" w:space="8" '
                   'w:color="BFBFBF"/></w:pBdr>')
    if tabs:
        ppr.append(tabs)
    # 编号段落不叠加报告缩进，否则会破坏编号的悬挂缩进
    if indent and not numbered:
        ppr.append('<w:ind w:left="%d"/>' % int(indent))

    ppr_xml = "<w:pPr>%s</w:pPr>" % "".join(ppr) if ppr else ""
    return "<w:p>%s%s</w:p>" % (ppr_xml, "".join(pieces))


def render_table(tbl, *, ctx, indent=0, rows=None, plain=False, quote=False):
    grid_el = tbl.find(q("tblGrid"))
    grid = []
    if grid_el is not None:
        for g in grid_el.findall(q("gridCol")):
            v = g.get(q("w"))
            if v and v.isdigit():
                grid.append(int(v))
    if not grid:
        ncol = max((len(r.findall(q("tc"))) for r, _m in table_rows(tbl)), default=4)
        grid = [9000 // max(ncol, 1)] * max(ncol, 1)

    rows_xml = []
    for tr, marker in (table_rows(tbl) if rows is None else rows):
        row_state = row_state_of(tr, marker)
        if plain and row_state == "del":
            continue
        if plain:
            row_state = None

        cells = []
        ci = 0
        for tc, span, vm in cell_spans(tr):
            width = sum(grid[ci:ci + span]) or grid[-1]
            ci += span

            tcPr_src = tc.find(q("tcPr"))
            cell_state = None
            if tcPr_src is not None and not plain:
                if tcPr_src.find(q("cellIns")) is not None:
                    cell_state = "ins"
                elif tcPr_src.find(q("cellDel")) is not None:
                    cell_state = "del"

            blocks = cell_blocks(tc)
            inner_changed = (not plain) and any(
                para_has_change(x) if x.tag == q("p") else table_changed(x)
                for x in blocks)

            fill = None
            if not plain:
                if row_state == "ins" or cell_state == "ins":
                    fill = FILL_ADD
                elif row_state == "del" or cell_state == "del":
                    fill = FILL_CELL_DEL
                elif inner_changed:
                    fill = FILL_CELL

            force_state = None if plain else (row_state or cell_state)

            tcpr = ['<w:tcW w:type="dxa" w:w="%d"/>' % width]
            if span > 1:
                tcpr.append('<w:gridSpan w:val="%d"/>' % span)
            if vm == "restart":
                tcpr.append('<w:vMerge w:val="restart"/>')
            elif vm == "continue":
                tcpr.append("<w:vMerge/>")
            if fill:
                tcpr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill)

            content = "".join(
                render_para(x, ctx=ctx, mark_para=False, compact=True,
                            force_state=force_state, plain=plain)
                if x.tag == q("p") else render_table(x, ctx=ctx, indent=0, plain=plain)
                for x in blocks)

            has_content = any(
                para_char_count(x) > 0 if x.tag == q("p") else True
                for x in blocks)
            if not has_content and cell_state in ("ins", "del"):
                content = "<w:p>%s</w:p>" % make_run(
                    "（新增单元格）" if cell_state == "ins" else "（单元格内容已删除）",
                    cell_state, {})

            cells.append("<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % (
                "".join(tcpr), content or "<w:p/>"))

        rows_xml.append("<w:tr>%s</w:tr>" % "".join(cells))

    tbl_pr = ('<w:tblPr><w:tblStyle w:val="TableGrid"/>'
              '<w:tblW w:type="auto" w:w="0"/>'
              '<w:tblInd w:type="dxa" w:w="%d"/>'
              '<w:tblLayout w:type="fixed"/>'
              '<w:tblLook w:firstColumn="1" w:firstRow="1" w:lastColumn="0" '
              'w:lastRow="0" w:noHBand="0" w:noVBand="1" w:val="04A0"/></w:tblPr>') % (
                  int(indent) + (120 if quote else 0))

    return "<w:tbl>%s%s%s</w:tbl>" % (
        tbl_pr,
        "<w:tblGrid>%s</w:tblGrid>" % "".join('<w:gridCol w:w="%d"/>' % w for w in grid),
        "".join(rows_xml))


# ---------------------------------------------------------------------------
# 被参考章节：修订内容里写了"参考 3.2 节 / 详见第 4 章 / 逻辑同 5.1"，
# 就把被引用的章节原文一并照录，便于评审时不回源文件
# ---------------------------------------------------------------------------

REF_VERB_PATTERN = (
    r"(?:参考|参见|参照|详见|(?<![意可所看遇])见|依据|按照|遵循|沿用|对照|比照"
    r"|(?<![相不协如])同)"
)
REF_NUM_TOKEN = r"\d{1,2}(?:\.\d{1,2}){1,3}"

# 引用动词必须紧贴章节号：允许 0~3 个空格，但不允许换行/其他字。
# 这样 "…文件一致\n1.6 txt文件内容…" 这类编号列表不会被当成引用。
REF_VERB_NUM_RE = re.compile(
    REF_VERB_PATTERN + r"[ \t\u3000]{0,3}(?<![\d.])(?P<num>" + REF_NUM_TOKEN + r")(?![\d.])")
# 章节号后紧跟"章/节/条"：不依赖动词，也是明确引用
# 排除"2.2 节点""3.1 条件/条款""1.2 章程"这类词内命中
REF_NUM_SUFFIX_RE = re.compile(
    r"(?<![\d.])(?P<num>" + REF_NUM_TOKEN + r")[ \t\u3000]{0,2}"
    r"(?=[章节条](?![点目约省奏日能制件例款形纹记程]))")
# "与 2.2 保持一致" / "和 3.1 相同"：章节号在前、比较词在后
REF_NUM_SAME_RE = re.compile(
    r"(?:与|和|同)[ \t\u3000]{0,2}(?<![\d.])(?P<num>" + REF_NUM_TOKEN
    + r")(?![\d.])[ \t\u3000]{0,4}(?:保持)?(?:一致|相同)")
REF_CHAPTER_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零〇]{1,4})\s*[章节条]")
REF_QUOTE_RE = re.compile(r"[《“\"]([^》”\"]{2,30})[》”\"]")

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s):
    """一 / 十二 / 二十三 -> 1 / 12 / 23；纯数字直接转"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    total = sec = 0
    for ch in s:
        if ch in CN_DIGITS:
            sec = CN_DIGITS[ch]
        elif ch == "十":
            total += (sec or 1) * 10
            sec = 0
        elif ch == "百":
            total += (sec or 1) * 100
            sec = 0
    return total + sec


def _norm_ref_title(t):
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*)[.、\s]*", "", t or "")
    t = re.sub(r"^第[一二三四五六七八九十百零〇\d]+[章节条]\s*", "", t or "")
    t = re.sub(r"[\s\u3000:：]+", "", t)
    return t.strip("。.，,；;、")


def heading_number(node, ctx):
    """节点标题的章节号：优先自动编号，其次标题里手打的数字"""
    label = ((ctx.get("labels") or {}).get(node.get("elem")) or "").strip()
    if label:
        return label.rstrip(".、 ")
    text = (node.get("text") or "").strip()
    m = re.match(r"^(\d+(?:\.\d+){0,3})[.、\s]", text)
    if m:
        return m.group(1)
    m = re.match(r"^第([一二三四五六七八九十百零〇\d]+)[章节条]", text)
    if m:
        return str(cn_to_int(m.group(1)))
    m = re.match(r"^([一二三四五六七八九十]+)[、.．]", text)
    if m:
        return str(cn_to_int(m.group(1)))
    return ""


def build_ref_index(tree, ctx):
    """标题索引：章节号 -> 节点列表、标题文字 -> 节点

    同一份文档里章节号可能重复（分卷、附录重排），因此按出现顺序保存，
    解析时取"引用位置之前最近的一个"。
    """
    num_idx, title_idx = {}, {}
    counter = [0]

    def walk(node):
        counter[0] += 1
        node["order"] = counter[0]
        if node.get("level") and node.get("elem") is not None:
            num = heading_number(node, ctx)
            if num:
                num_idx.setdefault(num, []).append(node)
                if not num.isdigit() and all(ch in "一二三四五六七八九十百零〇" for ch in num):
                    num_idx.setdefault(str(cn_to_int(num)), []).append(node)
            t = _norm_ref_title(node.get("text") or "")
            if len(t) >= 2:
                title_idx.setdefault(t, []).append(node)
        for c in node.get("children") or []:
            walk(c)

    walk(tree)
    return {"num": num_idx, "title": title_idx}


def _pick_ref(cands, node):
    """同号章节里取离引用位置最近的一个（优先前文）"""
    if not cands:
        return None
    order = node.get("order") if node is not None else None
    if order is None:
        return cands[0]
    before = [c for c in cands if c.get("order") is not None and c["order"] < order]
    after = [c for c in cands if c.get("order") is not None and c["order"] > order]
    if before and after:
        return before[-1] if (order - before[-1]["order"]) <= (after[0]["order"] - order) else after[0]
    return before[-1] if before else (after[0] if after else cands[0])


def _iter_para_inserted(p):
    return "".join(run_text(r) for r, st, _l in iter_runs(p) if st == "ins")


def node_inserted_text(node, ctx=None):
    """该节点自有内容里"新增"的文字（不含子节）

    目录条目要排除：目录里常出现"参考数据文件""参见 3.1.2.7"这类标题文字，
    若把目录当正文扫描，会把一堆章节误判成"被引用"。
    """
    chunks = []
    ctx = ctx or {}
    toc = ctx.get("toc") or {}
    styles = ctx.get("styles") or {}

    def is_toc_para(p):
        if id(p) in toc:
            return True
        try:
            return toc_level_of(p, styles) is not None
        except Exception:                                   # noqa: BLE001
            return False

    def scan_para(p):
        if is_toc_para(p):
            return
        t = _iter_para_inserted(p)
        if t.strip():
            chunks.append(t)

    def scan_tbl(tbl):
        for tr, _m in table_rows(tbl):
            for tc, _s, _v in cell_spans(tr):
                for b in cell_blocks(tc):
                    if b.tag == q("p"):
                        scan_para(b)
                    else:
                        scan_tbl(b)

    for b in node["blocks"]:
        if b["kind"] == "para":
            scan_para(b["elem"])
        else:
            scan_tbl(b["elem"])
    return "\n".join(chunks)


def find_refs_in_text(text, index, from_node=None):
    """从一段修订文字里找出被引用的章节 -> [(编号, 节点, 触发片段)]"""
    if not text:
        return []
    hits, seen = [], set()

    def add(label, node, start, end):
        key = id(node)
        if key in seen:
            return
        seen.add(key)
        # 取引用点前后一小段作为提示，去掉换行
        left, right = max(0, start - 26), min(len(text), end + 20)
        snippet = re.sub(r"\s+", " ", text[left:right]).strip()
        if left > 0:
            snippet = "…" + snippet
        if right < len(text):
            snippet = snippet + "…"
        if len(snippet) > 70:
            snippet = snippet[:70] + "…"
        hits.append((label, node, snippet))

    for m in REF_VERB_NUM_RE.finditer(text):
        token = m.group("num")
        target = _pick_ref(index["num"].get(token), from_node)
        if target is not None:
            add(token, target, m.start("num"), m.end("num"))

    for m in REF_NUM_SUFFIX_RE.finditer(text):
        token = m.group("num")
        target = _pick_ref(index["num"].get(token), from_node)
        if target is not None:
            add(token, target, m.start("num"), m.end("num"))

    for m in REF_NUM_SAME_RE.finditer(text):
        token = m.group("num")
        target = _pick_ref(index["num"].get(token), from_node)
        if target is not None:
            add(token, target, m.start("num"), m.end("num"))

    for m in REF_CHAPTER_RE.finditer(text):
        num = cn_to_int(m.group(1))
        target = _pick_ref(index["num"].get(str(num)), from_node)
        if target is not None:
            add(str(num), target, m.start(), m.end())

    for m in REF_QUOTE_RE.finditer(text):
        title = _norm_ref_title(m.group(1))
        target = _pick_ref(index["title"].get(title), from_node)
        if target is None:
            continue
        # 书名号/引号前必须紧贴引用动词（"参见《返回码》"），
        # 避免正文里普通的书名号被当成章节引用
        before = text[max(0, m.start() - 12):m.start()]
        if not re.search(REF_VERB_PATTERN + r"[ \t\u3000]{0,3}$", before):
            continue
        add(heading_number(target, {}) or title, target, m.start(), m.end())

    return hits


def is_ancestor(a, b):
    p = b.get("parent")
    while p is not None:
        if p is a:
            return True
        p = p.get("parent")
    return False


def collect_refs(tree, ctx):
    """{id(节点): [(编号, 目标节点, 片段)]}，只扫有改动的节点"""
    index = ctx.get("ref_index") or {"num": {}, "title": {}}
    out = {}

    def walk(node):
        if node.get("elem") is not None and node_changed(node):
            refs = []
            for label, target, snippet in find_refs_in_text(
                    node_inserted_text(node, ctx), index, node):
                if target is node or is_ancestor(target, node):
                    continue        # 自引用 / 指向自己的上级章节，不重复照录
                refs.append((label, target, snippet))
            if refs:
                out[id(node)] = refs
        for c in node.get("children") or []:
            walk(c)

    walk(tree)
    return out


def _is_covered(node, covered):
    """该章节是否已被前文完整照录（自己或某个上级已照录）"""
    if id(node) in covered:
        return True
    p = node.get("parent")
    while p is not None:
        if id(p) in covered:
            return True
        p = p.get("parent")
    return False


def _fully_covered(node, covered):
    """自己没正文、子节又都已照录过 —— 整节没有新内容可重复"""
    if id(node) in covered:
        return True
    if node.get("blocks"):
        return False
    ch = node.get("children") or []
    return bool(ch) and all(_fully_covered(c, covered) for c in ch)


def render_node_plain(node, parts, depth, ctx):
    """照录一个章节的原文：不标注修订、不裁剪表格

    已经在前文照录过的子节只留一行指引，避免整章重复。
    标题使用 RefHeading（加粗、编号文字保留、不进大纲目录），
    否则照录章节的原始编号会插进报告的章节号序列，看起来像"序号错乱"。
    """
    indent = INDENT_STEP * min(depth, 4)
    covered = ctx.setdefault("ref_covered", set())
    covered.add(id(node))
    parts.append(render_para(node["elem"], ctx=ctx, indent=indent,
                             style="RefHeading",
                             mark_para=False, plain=True, quote=True))
    render_blocks_plain(node["blocks"], parts, INDENT_STEP * min(depth + 1, 5), ctx)
    for c in node.get("children") or []:
        if id(c) in covered:
            parts.append(meta_line("%s：见前文，不再重复。" % (c.get("text") or ""),
                                   indent=INDENT_STEP * min(depth + 1, 5)))
            continue
        render_node_plain(c, parts, depth + 1, ctx)


def render_referenced(node, parts, indent, depth, ctx):
    refs = (ctx.get("refs") or {}).get(id(node))
    if not refs:
        return
    covered = ctx.setdefault("ref_covered", set())
    for label, target, snippet in refs:
        title = target.get("text") or ""
        # 标题里已经带章节号时不再重复 ("2.2 2.2 鉴权流程")
        head = title if title.startswith(label) else ("%s %s" % (label, title)).strip()
        if _is_covered(target, covered) or _fully_covered(target, covered):
            parts.append(meta_line(
                "被参考章节 %s：原文已在前文列出，此处不再重复。" % head,
                indent=indent))
            continue
        parts.append(meta_line(
            "被参考章节 %s —— 修订内容提到“%s”，以下照录原文（未标注改动）："
            % (head, snippet), indent=indent))
        render_node_plain(target, parts, depth + 1, ctx)
        parts.append(meta_line("—— 被参考内容结束 ——", indent=indent))


def render_blocks_plain(blocks, parts, indent, ctx):
    """原样照录：不裁剪表格、不丢弃未改动段落、不标注修订"""
    prev_empty = False
    for b in blocks:
        if b["kind"] == "para":
            p = b["elem"]
            if para_mark_state(p) == "del":
                continue
            is_empty = para_char_count(p) == 0 and not para_has_object(p)
            if is_empty and prev_empty:
                continue
            parts.append(render_para(p, ctx=ctx, indent=indent, plain=True, quote=True))
            prev_empty = is_empty
        else:
            parts.append(render_table(b["elem"], ctx=ctx, indent=indent, plain=True, quote=True))
            prev_empty = False


def render_blocks(blocks, parts, indent, ctx, heading_text=""):
    prev_empty = False
    toc = ctx.get("toc") or {}
    module_title = heading_text or ""
    for b in blocks:
        if b["kind"] == "para":
            p = b["elem"]

            # 目录条目：只保留目标小节有改动的条目，其余整条丢掉
            tinfo = toc.get(id(p))
            if tinfo is not None:
                if tinfo["keep"]:
                    ti = indent + (tinfo["level"] - 1) * TOC_INDENT_STEP
                    parts.append(render_para(
                        p, ctx=ctx, indent=ti, mark_para=False,
                        tabs='<w:tabs><w:tab w:val="right" w:leader="dot" '
                             'w:pos="%d"/></w:tabs>' % max(2000, TOC_TAB_WIDTH - ti)))
                    prev_empty = False
                continue

            # 未套标题样式的"文档审核记录"小标题：作为该模块的起始标记
            if is_audit_module_title(text_of(p)):
                module_title = text_of(p)
            strict = is_audit_module_title(module_title, allow_long=True)

            if strict and not para_has_change(p):
                # 审核记录模块：未改动的段落一律不输出（标题行本身保留）
                if text_of(p).strip() == (module_title or "").strip():
                    parts.append(render_para(p, ctx=ctx, indent=indent))
                    prev_empty = False
                continue

            is_empty = para_char_count(p) == 0 and not para_has_object(p)
            if is_empty and prev_empty:
                continue
            parts.append(render_para(p, ctx=ctx, indent=indent))
            prev_empty = is_empty
        else:
            tbl = b["elem"]
            strict = is_audit_module_title(module_title, allow_long=True)
            rows, _omitted, _kind, n_changed = record_rows(
                tbl, module_title, force_kind="review" if strict else None)
            if rows is not None and n_changed == 0:
                # 记录类表格整张都没改：不占版面，静默跳过
                continue
            parts.append(render_table(tbl, ctx=ctx, indent=indent, rows=rows))
            prev_empty = False


def toc_node_summary(node, ctx):
    """该节点是不是"目录"节点：是则返回 {entries, kept, omitted}，否则 None"""
    toc = ctx.get("toc") or {}
    groups = ctx.get("toc_groups") or {}
    if not toc:
        return None

    entries, gids = 0, []
    for b in node["blocks"]:
        if b["kind"] != "para":
            return None
        p = b["elem"]
        info = toc.get(id(p))
        if info is None:
            # 目录条目之间/之后的空段落不影响判断
            if para_char_count(p) == 0 and not para_has_object(p):
                continue
            return None
        entries += 1
        if info["group"] not in gids:
            gids.append(info["group"])
    if entries < 2:
        return None

    kept = sum(groups[g]["kept"] for g in gids if g in groups)
    omitted = sum(groups[g]["omitted"] for g in gids if g in groups)
    return {"entries": entries, "kept": kept, "omitted": omitted}


def render_node(node, parts, depth, ctx):
    for ch in node["children"]:
        if not node_changed(ch):
            continue
        indent = INDENT_STEP * min(depth, 4)
        parts.append(render_para(ch["elem"], ctx=ctx, indent=indent,
                                 style=style_for_level(ch["level"], 1),
                                 mark_para=False))

        summary = toc_node_summary(ch, ctx)
        if summary is not None:
            parts.append(meta_line(
                "本节为目录：共 %d 条条目，仅保留与本次修订相关的 %d 条"
                % (summary["entries"], summary["kept"]),
                indent=indent + 120))

        render_blocks(ch["blocks"], parts, INDENT_STEP * min(depth + 1, 5), ctx,
                      heading_text=ch["text"])
        render_referenced(ch, parts, INDENT_STEP * min(depth + 1, 5), depth, ctx)
        render_node(ch, parts, depth + 1, ctx)


# ---------------------------------------------------------------------------
# 输出 docx 框架文件
# ---------------------------------------------------------------------------

CT_DOC = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CT_STYLES = "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
CT_NUMBERING = "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"
CT_APP = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_NUMBERING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"


def build_content_types_xml(ctx, need_numbering):
    """按实际复制的部件生成 [Content_Types].xml"""
    exts = {"rels": "application/vnd.openxmlformats-package.relationships+xml",
            "xml": "application/xml"}
    for ext in ctx["exts"]:
        ct = ctx["ct_defaults"].get(ext) or EXT_FALLBACK.get(ext)
        if ct:
            exts[ext] = ct

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    for ext in sorted(exts):
        lines.append('  <Default Extension="%s" ContentType="%s"/>'
                     % (xml_escape(ext), xml_escape(exts[ext])))
    lines.append('  <Override PartName="/word/document.xml" ContentType="%s"/>' % CT_DOC)
    lines.append('  <Override PartName="/word/styles.xml" ContentType="%s"/>' % CT_STYLES)
    if need_numbering:
        lines.append('  <Override PartName="/word/numbering.xml" ContentType="%s"/>'
                     % CT_NUMBERING)
    for part in sorted(ctx["overrides"]):
        if part in ("word/document.xml", "word/styles.xml", "word/numbering.xml"):
            continue
        lines.append('  <Override PartName="/%s" ContentType="%s"/>'
                     % (xml_escape(part), xml_escape(ctx["overrides"][part])))
    lines.append('  <Override PartName="/docProps/core.xml" ContentType="%s"/>' % CT_CORE)
    lines.append('  <Override PartName="/docProps/app.xml" ContentType="%s"/>' % CT_APP)
    lines.append('</Types>')
    return "\n".join(lines) + "\n"


ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""


def build_document_rels_xml(ctx, need_numbering):
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
             '  <Relationship Id="rId1" Type="%s" Target="styles.xml"/>' % REL_STYLES]
    if need_numbering:
        lines.append('  <Relationship Id="rId2" Type="%s" Target="numbering.xml"/>'
                     % REL_NUMBERING)
    for rel in ctx["out_rels"]:
        mode = ' TargetMode="External"' if rel["mode"] == "External" else ""
        lines.append('  <Relationship Id="%s" Type="%s" Target="%s"%s/>'
                     % (xml_escape(rel["id"]), xml_escape(rel["type"]),
                        xml_escape(rel["target"], {'"': "&quot;"}), mode))
    lines.append('</Relationships>')
    return "\n".join(lines) + "\n"

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimSun" w:cs="Times New Roman"/><w:sz w:val="21"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/><w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading4">
    <w:name w:val="heading 4"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="120" w:after="80"/><w:outlineLvl w:val="3"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading5">
    <w:name w:val="heading 5"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="120" w:after="80"/><w:outlineLvl w:val="4"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="RefHeading">
    <w:name w:val="Quoted Heading"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:spacing w:before="160" w:after="80"/><w:ind w:left="360"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="404040"/></w:rPr>
  </w:style>
  <w:style w:type="table" w:styleId="TableGrid">
    <w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/><w:uiPriority w:val="39"/>
    <w:tblPr>
      <w:tblBorders>
        <w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        <w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        <w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        <w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        <w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        <w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>
      </w:tblBorders>
    </w:tblPr>
  </w:style>
  <w:style w:type="table" w:default="1" w:styleId="TableNormal">
    <w:name w:val="Normal Table"/><w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/>
    <w:tblPr><w:tblInd w:w="0" w:type="dxa"/>
      <w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar>
    </w:tblPr>
  </w:style>
</w:styles>
"""

SECT_PR = (
    '<w:sectPr>'
    '<w:pgSz w:w="12240" w:h="15840"/>'
    '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    '<w:cols w:space="720"/>'
    '<w:docGrid w:linePitch="360"/>'
    '</w:sectPr>'
)


def build_core_xml(title, subject, creator, created_iso, modified_iso):
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>%s</dc:title>
  <dc:subject>%s</dc:subject>
  <dc:creator>%s</dc:creator>
  <dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>
</cp:coreProperties>
""" % (xml_escape(title), xml_escape(subject), xml_escape(creator),
       created_iso, modified_iso)


APP_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/vt">
  <Application>extract_all_revisions.py</Application>
</Properties>
"""


# ---------------------------------------------------------------------------
# 分析源文档
# ---------------------------------------------------------------------------

def normalize_manual_deletions(root, styles_xml=None):
    """Treat manual strike/dstrike as deletion evidence without changing report layout."""
    style_root = ET.fromstring(styles_xml) if styles_xml else None
    styles = {e.get(q('styleId')): e for e in style_root.findall(q('style'))} if style_root is not None else {}
    parents = {c: p for p in root.iter() for c in p}
    def chain(sid):
        seen = set()
        while sid and sid not in seen and sid in styles:
            seen.add(sid)
            style = styles[sid]
            yield style.find(q('rPr'))
            base = style.find(q('basedOn'))
            sid = base.get(q('val')) if base is not None else None
    def style_id(props, tag):
        node = props.find(q(tag)) if props is not None else None
        return node.get(q('val')) if node is not None else None
    defaults = style_root.find('./' + q('docDefaults') + '/' + q('rPrDefault') + '/' + q('rPr')) if style_root is not None else None
    next_id = max([int(e.get(q('id'))) for e in root.iter() if (e.get(q('id')) or '').isdigit()] + [0]) + 1
    for run in list(root.iter(q('r'))):
        ancestors, parent = [], parents.get(run)
        while parent is not None:
            ancestors.append(parent); parent = parents.get(parent)
        if any(e.tag in (q('del'), q('moveFrom')) for e in ancestors):
            continue
        rpr = run.find(q('rPr'))
        paragraph = next((e for e in ancestors if e.tag == q('p')), None)
        ppr = paragraph.find(q('pPr')) if paragraph is not None else None
        default_style = next((sid for sid, e in styles.items() if e.get(q('type')) == 'paragraph' and e.get(q('default')) in ('1', 'true', 'on')), 'Normal')
        sources = [rpr] + list(chain(style_id(rpr, 'rStyle'))) + list(chain(style_id(ppr, 'pStyle') or default_style)) + [defaults]
        struck = False
        for tag in ('strike', 'dstrike'):
            for props in sources:
                node = props.find(q(tag)) if props is not None else None
                if node is not None:
                    struck = struck or node.get(q('val'), 'true').lower() not in ('0', 'false', 'off')
                    break
        if not struck:
            continue
        parent = parents.get(run)
        if parent is None:
            continue
        marker = ET.Element(q('del'), {q('id'): str(next_id), q('author'): '手工删除线'})
        next_id += 1
        for text in run.iter(q('t')):
            text.tag = q('delText')
        position = list(parent).index(run)
        parent.remove(run); marker.append(run); parent.insert(position, marker)


def analyze(src_path):
    src_name = os.path.basename(src_path)

    with zipfile.ZipFile(src_path, "r") as z:
        try:
            doc_xml = z.read("word/document.xml")
        except KeyError:
            raise RuntimeError("源文件缺少 word/document.xml，似乎不是合法的 docx")
        styles = load_styles(z)
        numbering = load_numbering(z)
        styles_xml = z.read('word/styles.xml') if 'word/styles.xml' in z.namelist() else None

    root = ET.fromstring(doc_xml)
    normalize_manual_deletions(root, styles_xml)

    allow_guess = not has_styled_heading(root, styles)
    labels = compute_numbering_labels(root, numbering, styles)
    tree = build_tree(root, styles, allow_guess, labels)

    st = count_node(tree)
    total = st["add"] + st["del"] + st["mod"]

    return {
        "src_name": src_name,
        "src_path": src_path,
        "root": root,
        "tree": tree,
        "styles": styles,
        "labels": labels,
        "stats": {
            "marks": count_marks(root),
            "style_revs": count_style_revs(root),
            "add": st["add"],
            "del": st["del"],
            "mod": st["mod"],
            "total": total,
            "para": st["para"],
            "table": st["table"],
            "chapters": top_chapters(tree),
            "headings": changed_heading_count(tree),
        },
        "guess_mode": allow_guess,
    }


# ---------------------------------------------------------------------------
# 生成报告 docx
# ---------------------------------------------------------------------------

def build_report_docx(info, out_path):
    now = datetime.datetime.now()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    src_name = info["src_name"]
    tree = info["tree"]
    st = info["stats"]

    with zipfile.ZipFile(info["src_path"], "r") as src_zip:
        ctx = make_ctx(src_zip, info["styles"], info.get("labels"))
        names = set(src_zip.namelist())

        # 目录条目：先算出"哪条要留、哪条要丢"
        if tree:
            ctx["toc"], ctx["toc_groups"] = annotate_toc(tree, info["styles"])
        else:
            ctx["toc"], ctx["toc_groups"] = {}, {}

        # 被参考章节：修订文字里 "参考 3.2 节 / 详见第 4 章" 之类
        ctx["ref_index"] = build_ref_index(tree, ctx)
        ctx["refs"] = collect_refs(tree, ctx)
        ctx["ref_covered"] = set()
        ref_targets = {id(t) for refs in ctx["refs"].values()
                       for _l, t, _s in refs}

        parts = []
        parts.append(xml_para("文档修订内容汇总报告", style="Title"))
        parts.append(xml_field_para("文档名称：", src_name))
        parts.append(xml_field_para("生成日期：", now.strftime("%Y-%m-%d")))
        parts.append(spacer())

        # 一、总体情况
        parts.append(xml_para("一、修订总体情况", style="Heading1"))
        if st["total"] == 0:
            parts.append(xml_para("未在文档中发现任何文字修订。"))
        else:
            parts.append(xml_para(
                "报告按原文档模块组织：只输出有改动的小节，但小节内的内容完整保留，"
                "改动在原文位置就地标注。版本历史 / 修订记录表、文档审核 / 评审 / 会签"
                "记录表只保留表头和有改动的行；\"文档审核记录\"这类模块只输出改动部分，"
                "未改动的段落与表格行不再重复；目录只保留有改动小节的条目。"))
            if ref_targets:
                parts.append(xml_para(
                    "修订内容中提到“参考某章节”的，报告会在该小节后照录被引用章节的原文"
                    "（不标注修订），共涉及 %d 个章节，便于对照阅读。" % len(ref_targets)))
        if info.get("guess_mode"):
            parts.append(meta_line(
                "提示：源文档未使用 Word 内置标题样式，本文的层级由标题文本形态推断。"))
        parts.append(spacer())

        # 二、修订内容
        parts.append(xml_para("二、修订内容（按原文档模块，完整保留小节内容）",
                              style="Heading1"))
        if st["total"] == 0:
            parts.append(meta_line("（源文档没有修订内容，本节省略）"))
        else:
            parts.append(xml_para(
                "标注说明：绿色文字 + 浅绿底纹 = 新增；灰色文字 + 删除线 = 删除；"
                "段落左侧竖线 = 该段有改动。图片、自动编号、超链接均按原文保留。"
                "版本历史表、审核 / 评审记录表只列出有改动的行，\"文档审核记录\"模块"
                "只保留改动内容；目录只列出有改动小节的条目。"
                "未标注的内容为原文，保持原样。"))
        parts.append(spacer())

        root_changed = [b for b in tree["blocks"] if block_changed(b)]
        if root_changed:
            toc_ids = ctx.get("toc") or {}
            only_toc = all(b["kind"] == "para" and id(b["elem"]) in toc_ids
                           for b in root_changed)
            if not only_toc:
                # 只改目录时不再加这句话，由下方的目录提示行说明
                parts.append(meta_line("文档开头（不属于任何标题）的内容："))
            render_blocks(tree["blocks"], parts, 0, ctx)

        render_node(tree, parts, 0, ctx)

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document %s><w:body>%s%s</w:body></w:document>'
        ) % (DOC_NS_DECL, "".join(parts), SECT_PR)

        need_numbering = bool(ctx["numbering"]) and "word/numbering.xml" in names
        content_types_xml = build_content_types_xml(ctx, need_numbering)
        document_rels_xml = build_document_rels_xml(ctx, need_numbering)

        tmp_path = out_path + ".tmp"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.writestr("[Content_Types].xml", content_types_xml)
            zout.writestr("_rels/.rels", ROOT_RELS_XML)
            zout.writestr("word/_rels/document.xml.rels", document_rels_xml)
            zout.writestr("word/document.xml", document_xml)
            zout.writestr("word/styles.xml", STYLES_XML)
            if need_numbering:
                zout.writestr("word/numbering.xml", src_zip.read("word/numbering.xml"))
            zout.writestr("docProps/core.xml", build_core_xml(
                title="文档修订内容汇总报告",
                subject="%s 修订内容整合" % src_name,
                creator="extract_all_revisions.py",
                created_iso=now_iso,
                modified_iso=now_iso,
            ))
            zout.writestr("docProps/app.xml", APP_XML)

            written = {
                "[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels",
                "word/document.xml", "word/styles.xml", "docProps/core.xml",
                "docProps/app.xml", "word/numbering.xml",
            }
            for part in sorted(set(ctx["parts"]) | ctx["extra_parts"]):
                if part in written or part not in names:
                    continue
                zout.writestr(part, src_zip.read(part))

    if os.path.exists(out_path):
        os.remove(out_path)
    os.replace(tmp_path, out_path)

    return {
        "media_parts": sum(1 for p in ctx["parts"] if p.startswith("word/media/")),
        "all_parts": len(ctx["parts"]),
        "numbering": need_numbering,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) == 1:
        print(__doc__)
        sys.exit(0)

    ap = argparse.ArgumentParser(
        description="读取 docx 中的全部修订，按原文档标题层级整合输出报告。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python extract_all_revisions.py test.docx\n"
            "  python extract_all_revisions.py test.docx report.docx\n"
        ),
    )
    ap.add_argument("source", help="含修订记录的源 .docx 文件路径")
    ap.add_argument("output", nargs="?", default=None,
                    help="输出 .docx 路径（可选；省略则写到源文件同目录下）")
    args = ap.parse_args()

    src_path = os.path.abspath(args.source)
    if not os.path.isfile(src_path):
        print("错误：找不到源文件：%s" % src_path, file=sys.stderr)
        sys.exit(2)

    if args.output:
        out_path = os.path.abspath(args.output)
    else:
        base, _ = os.path.splitext(src_path)
        out_path = base + "_修订内容汇总报告.docx"

    try:
        info = analyze(src_path)
        extra = build_report_docx(info, out_path)
    except zipfile.BadZipFile:
        print("错误：不是有效的 .docx 文件（无法作为 zip 打开）：%s" % src_path,
              file=sys.stderr)
        sys.exit(1)
    except ET.ParseError as e:
        print("错误：word/document.xml 解析失败：%s" % e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("错误：处理失败：%s" % e, file=sys.stderr)
        sys.exit(1)

    print("源文件：%s" % src_path)
    if extra.get("all_parts"):
        print("已复制图片/图表/嵌入对象部件：%d 个（其中图片 %d 个）"
              % (extra["all_parts"], extra["media_parts"]))
    if extra.get("numbering"):
        print("自动编号：已保留")
    if info.get("guess_mode"):
        print("提示：源文档未使用标题样式，层级按标题文本形态推断。")
    print("报告已生成：%s" % out_path)


if __name__ == "__main__":
    main()
