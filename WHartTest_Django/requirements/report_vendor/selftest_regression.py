#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_all_revisions.py 回归自测

内置用例覆盖已经修复过的缺陷，防止后续改动把它们带回来：

  C1  被参考章节的标题必须用 RefHeading 输出，
      不得作为 Heading 进入报告章节序列（否则报告编号会"错乱"/倒退）
  C2  段落标记被删除的段落不占用自动编号（否则删除一个标题后整体编号偏移）
  C3  mc:Fallback 里重复的段落不重复计数
  C4  报告不再输出任何裁剪说明文案
      （"共 N 处改动""无改动的子节已省略""仅列出有改动的行"等）
  C5  目录条目里的"参考数据文件""参见 3.1.2.7"不得触发章节照录
  C6  "…文件一致" 后跟编号列表（1.6 xxx）不得被当成章节引用

用法：
  python selftest_regression.py                 # 跑内置用例（自动生成 fixture）
  python selftest_regression.py --keep          # 保留 fixture 便于排查
  python selftest_regression.py --docs a.docx b.docx
                                                # 对真实文档生成报告并做结构体检

退出码：0 = 全部通过；1 = 有失败项。
"""

import argparse
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "extract_all_revisions.py")

NSDECL = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>
"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/><w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:qFormat/>
    <w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="TOC1">
    <w:name w:val="toc 1"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8640"/></w:tabs></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="TOC2">
    <w:name w:val="toc 2"/><w:basedOn w:val="Normal"/>
  </w:style>
</w:styles>
"""

NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1."/><w:lvlJc w:val="left"/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1.%2"/><w:lvlJc w:val="left"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>
"""

# 章标题与节标题各用一个 numId（章带 startOverride=3），但两者指向同一个
# abstractNum —— 节号必须继承章号（3.1 / 3.3），不能各自从 1 开始
NUMBERING_SHARED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1."/><w:lvlJc w:val="left"/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1.%2"/><w:lvlJc w:val="left"/></w:lvl>
    <w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1.%2.%3"/><w:lvlJc w:val="left"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="0"/>
    <w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num>
</w:numbering>
"""

# 人工"重新开始编号"：numId=2 与 numId=1 共用 abstract 0，但带 startOverride=1
NUMBERING_RESTART = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1"/><w:lvlJc w:val="left"/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1.%2"/><w:lvlJc w:val="left"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="0"/>
    <w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>
</w:numbering>
"""

SECT = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')


# ---------------------------------------------------------------------------
# fixture 构造
# ---------------------------------------------------------------------------

def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def run(text, ins=False, deleted=False):
    tag = "w:delText" if deleted else "w:t"
    r = '<w:r><%s xml:space="preserve">%s</%s></w:r>' % (tag, esc(text), tag)
    if deleted:
        return ('<w:del w:id="3" w:author="tester" w:date="2024-01-01T00:00:00Z">'
                '%s</w:del>' % r)
    if ins:
        return ('<w:ins w:id="2" w:author="tester" w:date="2024-01-01T00:00:00Z">'
                '%s</w:ins>' % r)
    return r


def para(inner="", style=None, num=False, ilvl=0, num_id="1", mark_deleted=False):
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % style)
    if num:
        ppr.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%s"/></w:numPr>'
                   % (ilvl, num_id))
    if mark_deleted:
        ppr.append('<w:rPr><w:del w:id="9" w:author="tester" '
                   'w:date="2024-01-01T00:00:00Z"/></w:rPr>')
    return "<w:p>%s%s</w:p>" % (
        ("<w:pPr>%s</w:pPr>" % "".join(ppr)) if ppr else "", inner)


def build_fixture(path, body, numbering=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document %s><w:body>%s%s</w:body></w:document>'
                   % (NSDECL, body, SECT))
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/numbering.xml", numbering or NUMBERING)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)


# ---------------------------------------------------------------------------
# 读取生成结果
# ---------------------------------------------------------------------------

def doc_root(path):
    return ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))


def para_style(p):
    ppr = p.find(W + "pPr")
    st = ppr.find(W + "pStyle") if ppr is not None else None
    return (st.get(W + "val") or "") if st is not None else ""


def para_text(p):
    return "".join(t.text or "" for t in p.iter(W + "t")).strip()


def headings(path):
    """[(级别, 文本, styleId)]，只算 Heading1..5"""
    out = []
    for p in doc_root(path).iter(W + "p"):
        sid = para_style(p)
        m = re.match(r"^Heading([1-5])$", sid)
        if m:
            out.append((int(m.group(1)), para_text(p), sid))
    return out


def paras_with_style(path, style):
    return [para_text(p) for p in doc_root(path).iter(W + "p")
            if para_style(p) == style]


def full_text(path):
    return "\n".join(para_text(p) for p in doc_root(path).iter(W + "p"))


def audit_report(path, check_depth=True):
    """报告结构体检：重复编号 / 编号倒退 / 编号深度与标题级别不符

    只把"1.1 标题""1. 标题""1 标题"这类章节号当编号；
    "1、首页""5) 交易"属于中文列表序号，不参与判断，避免误报。
    """
    issues = []
    seen = {}
    prev = None
    for lvl, text, _sid in headings(path):
        m = re.match(r"^(\d+(?:\.\d+)*)(?:\.\s*|\s+|$)", text)
        if not m:
            continue
        num = m.group(1)
        tup = tuple(int(x) for x in num.split("."))
        if tup in seen:
            issues.append("重复编号 %s：%s / 前文 %s" % (num, text[:30], seen[tup][:30]))
        seen[tup] = text
        if prev is not None and len(tup) <= len(prev) and tup < prev:
            issues.append("编号倒退 %s：%s（上一节 %s）"
                          % (num, text[:30], ".".join(map(str, prev))))
        prev = tup
        # 报告整体比源文档下沉一级：源 H1 -> Heading2
        # 只查"编号比标题级别浅太多"的情况（编号深于标题级别在真实文档中常见）
        if check_depth and len(tup) < lvl - 1:
            issues.append("编号深度不符 %s：%s（Heading%d）" % (num, text[:30], lvl))
    return issues


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def case_ref_heading(tool, workdir):
    """C1：被参考章节的标题不能混进报告章节序列"""
    body = (
        para(run("1 概述"), style="Heading1")
        + para(run("1.1 目的"), style="Heading2")
        + para(run("本流程的鉴权处理参考 2.2 鉴权流程实现，保持与既有接口一致。",
                   ins=True))
        + para(run("2.2 鉴权流程"), style="Heading2")
        + para(run("系统采用签名加时间戳的方式校验请求来源。"))
    )
    src = os.path.join(workdir, "c1_src.docx")
    out = os.path.join(workdir, "c1_out.docx")
    build_fixture(src, body)
    tool.build_report_docx(tool.analyze(src), out)

    refs = [t for t in paras_with_style(out, "RefHeading") if "鉴权流程" in t]
    assert refs, "未找到用 RefHeading 输出的被参考章节标题"

    bad = [t for _l, t, _s in headings(out) if "鉴权流程" in t]
    assert not bad, "被参考章节标题混进了报告大纲：%r" % bad

    xml = zipfile.ZipFile(out).read("word/styles.xml").decode("utf-8")
    assert 'w:styleId="RefHeading"' in xml, "报告样式中缺少 RefHeading"
    assert 'w:styleId="RefHeading"' in xml and "outlineLvl" not in \
        xml.split('w:styleId="RefHeading"')[1].split("</w:style>")[0], \
        "RefHeading 不应带 outlineLvl"

    issues = audit_report(out)
    assert not issues, "报告编号异常：%s" % "；".join(issues)


def case_deleted_para_mark(tool, workdir):
    """C2：段落标记被删除的段落不占自动编号"""
    body = (
        para(run("概述"), style="Heading1", num=True)
        + para(run("已删除章节", deleted=True), style="Heading1", num=True,
               mark_deleted=True)
        + para(run("接口说明"), style="Heading1", num=True)
        + para(run("新增：查询接口返回字段说明。", ins=True))
    )
    src = os.path.join(workdir, "c2_src.docx")
    out = os.path.join(workdir, "c2_out.docx")
    build_fixture(src, body)

    info = tool.analyze(src)
    target = [p for p in info["root"].iter(W + "p") if para_text(p) == "接口说明"]
    assert target, "fixture 中找不到「接口说明」标题"
    label = (info["labels"].get(target[0]) or "").strip().rstrip(".、 ")
    assert label == "2", "删除段落后编号未回退：期望 2，实际 %r" % label

    tool.build_report_docx(info, out)
    text = full_text(out)
    assert re.search(r"(?m)^2[.、]?\s*接口说明", text), \
        "报告中未出现「2 接口说明」，实际标题行：%r" % [
            t for _l, t, _s in headings(out) if "接口说明" in t]
    assert not re.search(r"(?m)^3[.、]?\s*接口说明", text), \
        "报告中出现「3 接口说明」，编号被多算一位"


def case_fallback_dup(tool, workdir):
    """C3：mc:Fallback 中的重复段落不重复计数"""
    inner_p = para(run("文本框内标题"), num=True)
    alt = (
        '<w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="wps"><w:drawing><wps:txbx>'
        '<w:txbxContent>%s</w:txbxContent></wps:txbx></w:drawing></mc:Choice>'
        '<mc:Fallback><w:pict><v:shape><v:textbox>'
        '<w:txbxContent>%s</w:txbxContent></v:textbox></v:shape></w:pict>'
        '</mc:Fallback></mc:AlternateContent></w:r></w:p>' % (inner_p, inner_p)
    )
    body = (
        para(run("概述"), style="Heading1", num=True)
        + alt
        + para(run("接口说明"), style="Heading1", num=True)
        + para(run("新增：补充说明。", ins=True))
    )
    src = os.path.join(workdir, "c3_src.docx")
    build_fixture(src, body)

    info = tool.analyze(src)
    target = [p for p in info["root"].iter(W + "p") if para_text(p) == "接口说明"]
    assert target, "fixture 中找不到「接口说明」标题"
    label = (info["labels"].get(target[0]) or "").strip().rstrip(".、 ")
    # 正序：概述 1、文本框 2、接口说明 3；若 Choice/Fallback 各数一次则为 4
    assert label == "3", ("Fallback 被重复计数：期望 3，实际 %r"
                          "（文本框标题应只占一个编号）" % label)


def case_no_boilerplate(tool, workdir):
    """C4：报告不再出现任何"省略 / 改动条数"说明"""
    body = (
        para(run("1 概述"), style="Heading1")
        + para(run("1.1 目的"), style="Heading2")
        + para(run("本段为新增内容，说明报告裁剪规则。", ins=True))
        + para(run("1.2 未改动小节"), style="Heading2")
        + para(run("这段没有改动。"))
    )
    src = os.path.join(workdir, "c4_src.docx")
    out = os.path.join(workdir, "c4_out.docx")
    build_fixture(src, body)
    tool.build_report_docx(tool.analyze(src), out)

    text = full_text(out)
    banned = ["处改动", "已省略", "无改动的子节", "仅列出有改动的行", "行均无改动"]
    hits = [b for b in banned if b in text]
    assert not hits, "报告仍含裁剪说明文案：%r" % hits


def case_toc_no_ref(tool, workdir):
    """C5：目录条目里的"参考数据文件 / 参见 3.1.2.7"不得触发照录"""
    body = (
        para(run("目录"), style="Heading1")
        # 目录条目整段是新增的，且标题文字里含"参考数据文件""参见"
        + para(run("3.1.1.11 综合业务系统参考数据文件 31 3.1.1.12 综业CPXX数据文件 31",
                   ins=True), style="TOC1")
        + para(run("4.1.1 产品基础信息文件（参见3.1.2.7） 229", ins=True), style="TOC1")
        + para(run("3 接口"), style="Heading1")
        + para(run("3.1 接口说明"), style="Heading2")
        + para(run("新增：查询接口补充 requestId 字段。", ins=True))
        + para(run("3.1.1.12 综业CPXX数据文件"), style="Heading2")
        + para(run("字段说明见下表。"))
        + para(run("3.1.2.7 综业平台持仓余额文件"), style="Heading2")
        + para(run("字段说明见下表。"))
    )
    src = os.path.join(workdir, "c5_src.docx")
    out = os.path.join(workdir, "c5_out.docx")
    build_fixture(src, body)
    tool.build_report_docx(tool.analyze(src), out)

    refs = paras_with_style(out, "RefHeading")
    assert not refs, "目录条目被当成引用，照录了章节：%r" % refs
    assert "被参考章节" not in full_text(out), "报告里出现了「被参考章节」说明"


def case_list_number_no_ref(tool, workdir):
    """C6："…文件一致" 后跟编号列表项不得被当成章节引用"""
    body = (
        para(run("10 跑批"), style="Heading1")
        + para(run("10.2 需求描述"), style="Heading2")
        + para(run("生成的文件与 zsgg1yyyyMMdd001.txt 文件一致", ins=True))
        + para(run("1.6 txt文件内容换行符替换成window格式", ins=True))
        + para(run("1 概述"), style="Heading1")
        + para(run("1.6 用户操作步骤"), style="Heading2")
        + para(run("打开页面，按提示操作。"))
    )
    src = os.path.join(workdir, "c6_src.docx")
    out = os.path.join(workdir, "c6_out.docx")
    build_fixture(src, body)
    tool.build_report_docx(tool.analyze(src), out)

    refs = paras_with_style(out, "RefHeading")
    assert not refs, "编号列表项被误判为章节引用：%r" % refs
    text = full_text(out)
    assert "第2章" not in text and "被参考章节 1.6" not in text


def case_chapter_reset_shared(tool, workdir):
    """C7：章标题与节标题分属不同 numId、却共用同一个 abstractNum

    章用 numId=2（startOverride=3），节用 numId=1。Word 认为两者是同一条
    编号序列，节号会继承章号；按 numId 分别计数则章号不参与，节号从 1 开始，
    "3.3 预处理" 会变成 "1.6 / 1.7"。
    """
    body = (
        para(run("功能性需求"), style="Heading1", num=True, ilvl=0, num_id="2")
        + para(run("系统划分"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("子模块划分"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("预处理"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("概述"), style="Heading3", num=True, ilvl=2, num_id="1")
        + para(run("新增：预处理功能说明。", ins=True))
    )
    src = os.path.join(workdir, "c7_src.docx")
    out = os.path.join(workdir, "c7_out.docx")
    build_fixture(src, body, numbering=NUMBERING_SHARED)
    tool.build_report_docx(tool.analyze(src), out)

    texts = [t for _l, t, _s in headings(out)]
    assert any(t.startswith("3.3 ") for t in texts), \
        "节号未继承章号，3.3 丢失：%r" % texts
    assert not [t for t in texts if re.match(r"^1\.\d", t)], \
        "节号从 1 重新开始（3.3 变成 1.x）：%r" % texts


def case_manual_restart_override(tool, workdir):
    """C8：人工"重新开始编号 / 设置编号值"（startOverride）必须重启计数器

    numId=2 与 numId=1 共用同一个 abstractNum；在 Word 里对第二段列表
    右键"重新开始编号"后，XML 里 numId=2 会出现 startOverride。
    若因"共用 abstractNum"而忽略它，列表二会被算成 4、5（Word 显示 1、2）。
    """
    body = (
        para(run("列表一 甲"), num=True, ilvl=0, num_id="1")
        + para(run("列表一 乙"), num=True, ilvl=0, num_id="1")
        + para(run("列表一 丙"), num=True, ilvl=0, num_id="1")
        + para(run("列表二 甲"), num=True, ilvl=0, num_id="2")
        + para(run("列表二 乙"), num=True, ilvl=0, num_id="2")
    )
    src = os.path.join(workdir, "c8_src.docx")
    build_fixture(src, body, numbering=NUMBERING_RESTART)
    info = tool.analyze(src)

    labels = {}
    for p in tool._iter_number_paras(info["root"]):
        t = tool.text_of(p).strip()
        if t:
            labels[t] = info["labels"].get(p, "")
    assert labels.get("列表二 甲") == "1" and labels.get("列表二 乙") == "2", \
        "人工重启编号未生效：%r" % labels


def case_ancestor_materialized(tool, workdir):
    """C9：二级标题先于一级标题出现时，一级计数要"落地"为起始值

    Word 里先出现 1.1，随后第一个一级标题是 2（不是 1）——二级标题出现时
    Word 已把一级计数器落到了起始值 1。只在显示时回退起始值、不写回计数器，
    会把 2.2 / 2.3.1 一路算成 1.1 / 1.3.1。
    """
    body = (
        para(run("目的"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("范围"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("项目概述"), style="Heading1", num=True, ilvl=0, num_id="1")
        + para(run("系统目标"), style="Heading2", num=True, ilvl=1, num_id="1")
        + para(run("新增：系统目标说明。", ins=True))
    )
    src = os.path.join(workdir, "c9_src.docx")
    build_fixture(src, body)
    info = tool.analyze(src)

    labels = {}
    for p in tool._iter_number_paras(info["root"]):
        t = tool.text_of(p).strip()
        lab = info["labels"].get(p, "")
        if t and lab:
            labels[t] = lab
    want = {"目的": "1.1", "范围": "1.2", "项目概述": "2.", "系统目标": "2.1"}
    assert labels == want, "编号与 Word 不一致：%r" % labels


CASES = [
    ("C1 被参考章节标题不进入报告大纲", case_ref_heading),
    ("C2 段落标记删除的段落不占编号", case_deleted_para_mark),
    ("C3 mc:Fallback 副本不重复计数", case_fallback_dup),
    ("C4 报告无裁剪说明文案", case_no_boilerplate),
    ("C5 目录条目不触发章节照录", case_toc_no_ref),
    ("C6 编号列表项不当成章节引用", case_list_number_no_ref),
    ("C7 章/节共用 abstractNum 时节号继承章号", case_chapter_reset_shared),
    ("C8 人工重启编号（startOverride）生效", case_manual_restart_override),
    ("C9 二级标题先出现时一级计数落地为起始值", case_ancestor_materialized),
]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def load_tool():
    if not os.path.isfile(TOOL):
        sys.exit("找不到 extract_all_revisions.py：%s" % TOOL)
    spec = importlib.util.spec_from_file_location("extract_all_revisions", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_builtin(tool, keep):
    workdir = tempfile.mkdtemp(prefix="docx_rev_selftest_")
    failed = 0
    print("内置回归用例（fixture: %s）" % workdir)
    try:
        for name, fn in CASES:
            print("- %s ... " % name, end="", flush=True)
            try:
                fn(tool, workdir)
                print("PASS")
            except AssertionError as e:
                failed += 1
                print("FAIL\n    %s" % e)
            except Exception as e:                      # noqa: BLE001
                failed += 1
                print("ERROR\n    %s: %s" % (type(e).__name__, e))
    finally:
        if keep:
            print("fixture 已保留：%s" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    total = len(CASES)
    print("\n结果：%d/%d 通过" % (total - failed, total))
    return failed


def run_docs(tool, docs, strict=False):
    workdir = tempfile.mkdtemp(prefix="docx_rev_audit_")
    bad = warn = 0
    try:
        for doc in docs:
            name = os.path.basename(doc)
            print("- %s ... " % name, end="", flush=True)
            if not os.path.isfile(doc):
                bad += 1
                print("SKIP（文件不存在）")
                continue
            out = os.path.join(workdir, "out_%d.docx" % abs(hash(doc)))
            try:
                tool.build_report_docx(tool.analyze(doc), out)
                assert zipfile.ZipFile(out).testzip() is None
            except Exception as e:                      # noqa: BLE001
                bad += 1
                print("ERROR %s: %s" % (type(e).__name__, e))
                continue
            # 真实文档常有"分卷 / 附录里从 1 重新编号"这类结构，
            # 不做"编号深度 vs 标题级别"的严格校验，编号重复/倒退只提示
            issues = audit_report(out, check_depth=False)
            if issues:
                warn += len(issues)
                if strict:
                    bad += 1
                print("WARN（%d 项，多为分卷重新编号）" % len(issues))
                for it in issues[:4]:
                    print("    %s" % it)
            else:
                print("PASS（%d 个标题）" % len(headings(out)))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print("\n结果：%d 份文档生成失败，%d 项编号提示" % (bad, warn))
    return bad


def main():
    ap = argparse.ArgumentParser(description="extract_all_revisions.py 回归自测")
    ap.add_argument("--docs", nargs="+", metavar="DOCX",
                    help="对真实文档生成报告并做结构体检")
    ap.add_argument("--keep", action="store_true", help="保留测试 fixture")
    ap.add_argument("--strict", action="store_true",
                    help="--docs 模式下把编号提示也视为失败")
    args = ap.parse_args()

    tool = load_tool()
    if args.docs:
        failed = run_docs(tool, args.docs, strict=args.strict)
    else:
        failed = run_builtin(tool, args.keep)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
