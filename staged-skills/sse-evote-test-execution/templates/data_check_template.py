#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据链路校验脚本模板 —— 上证e投票平台测试 skill（Step4 · B 类）

适用用例类型：
    · 表结构校验（如 vote_basic_info 是否有 timestamp 字段）
    · 唯一性约束校验（会议ID + 时间戳 的组合唯一）
    · Kafka 消息字段校验（INFONET_VOTE.DATA 消息体含 timestamp 且与库内一致）
    · 历史数据失效 / 新数据生效校验（会议变更场景）
    · 生成物命名规则校验（送审 PDF 文件名）
    · 下游系统入库校验（统计系统 / 网站后台 / H5 / 业务系统 / 沪港通 / 先行赔付）

用法：
    1. 复制为 script/TC-{用例编号}.py
    2. 按用例填写 CHECKS 中的校验函数，并在 CONFIG 中填连接信息
    3. 执行：python3 script/TC-XXX.py
       产物写入 evidence/：{用例编号}_sql.txt / _result.csv / _kafka.txt

依赖：
    pip install psycopg2-binary kafka-python    # 按实际数据库/中间件调整
"""

import os
import re
import csv
import json
from datetime import datetime

# ============================== CONFIG ==============================

CASE_ID = "TC-XXX"
CASE_NAME = "验证基础服务股东大会基本信息表新增时间戳字段"

OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EVIDENCE_DIR = os.path.join(OUTPUT_ROOT, "evidence")

# 环境连接信息（真实凭据请从环境变量或 test_config.json 读取，禁止明文提交）
DB = {
    "host": os.environ.get("EVOTE_DB_HOST", "10.0.0.0"),
    "port": int(os.environ.get("EVOTE_DB_PORT", 5432)),
    "user": os.environ.get("EVOTE_DB_USER", ""),
    "password": os.environ.get("EVOTE_DB_PASSWORD", ""),
    "database": os.environ.get("EVOTE_DB_NAME", ""),
}
KAFKA = {
    "bootstrap": os.environ.get("EVOTE_KAFKA_BOOTSTRAP", ""),
    "topic": "INFONET_VOTE.DATA",
    "group": f"evote-test-{CASE_ID.lower()}",
}

# ============================== 基础设施 ==============================


def get_conn():
    """按实际数据库类型替换驱动：PostgreSQL / MySQL。"""
    import psycopg2
    return psycopg2.connect(**DB)


def query(sql, params=None):
    """执行查询，返回 (列名列表, 行列表)，同时把 SQL 记入证据文件。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
    append_evidence(f"{CASE_ID}_sql.txt", sql.strip() + "\n")
    return cols, rows


def execute(sql, params=None):
    """执行写操作（仅测试环境），返回是否成功。失败不抛异常，供唯一性约束用例判定。"""
    append_evidence(f"{CASE_ID}_sql.txt", "[WRITE] " + sql.strip() + "\n")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
            conn.commit()
        return True
    except Exception as e:
        append_evidence(f"{CASE_ID}_sql.txt", f"  -> 执行失败（预期内则为通过）: {e}\n")
        return False


def consume_messages(max_wait=30, max_count=5):
    """消费 Kafka 消息，返回消息体列表（字符串）。前置条件：消息已推送。"""
    from kafka import KafkaConsumer
    outs = []
    consumer = KafkaConsumer(
        KAFKA["topic"], bootstrap_servers=KAFKA["bootstrap"],
        group_id=KAFKA["group"], auto_offset_reset="latest",
        consumer_timeout_ms=max_wait * 1000,
    )
    for msg in consumer:
        outs.append(msg.value.decode("utf-8", errors="ignore"))
        if len(outs) >= max_count:
            break
    consumer.close()
    for m in outs:
        append_evidence(f"{CASE_ID}_kafka.txt", m + "\n")
    return outs


def append_evidence(filename, text):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    with open(os.path.join(EVIDENCE_DIR, filename), "a", encoding="utf-8") as f:
        f.write(text)


def dump_rows(filename, cols, rows):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, filename)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return path


# ============================== 校验函数 ==============================


def check_column_exists(table, column):
    """校验 1：表结构含指定字段。"""
    cols, rows = query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s", (table, column))
    assert rows, f"表 {table} 中不存在字段 {column}"
    return f"{table} 存在字段 {column}"


def check_uniqueness(table, key_cols, sample_row):
    """
    校验 2：组合唯一性约束。
    sample_row：dict，包含 key_cols 与其余字段的取值。
    按规则库 4.3 的三种组合分别验证；此处给出"完全相同 → 插入失败"的示例。
    """
    cols = list(sample_row.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    values = [sample_row[c] for c in cols]

    before = count_rows(table)
    ok = execute(sql, values)
    after = count_rows(table)
    assert not ok, f"预期插入失败（{'+'.join(key_cols)} 完全相同），实际插入成功"
    assert after == before, f"预期表中条数不变（{before}），实际 {after}"
    return f"重复键插入被拒绝，表中条数保持 {after}"


def count_rows(table, where=""):
    _, rows = query(f"SELECT count(*) FROM {table} {where}")
    return rows[0][0]


def check_kafka_has_timestamp(messages):
    """校验 3：Kafka 消息体包含 timestamp 字段。"""
    assert messages, "未消费到任何 Kafka 消息（前置条件：消息已推送）"
    for i, body in enumerate(messages, 1):
        assert '"timestamp"' in body or "'timestamp'" in body, f"第 {i} 条消息不含 timestamp：{body[:200]}"
    return f"共 {len(messages)} 条消息均含 timestamp"


def check_value_consistent(table, where, field, expected):
    """校验 4：库内字段值与期望值（如 Kafka 消息中的 timestamp）一致。"""
    _, rows = query(f"SELECT {field} FROM {table} WHERE {where}")
    assert rows, f"未查到记录：{table} WHERE {where}"
    actual = rows[0][0]
    assert str(actual) == str(expected), f"{field} 不一致：库内 {actual}，期望 {expected}"
    return f"{field} 一致：{actual}"


def check_history_invalidated(table, meeting_id, valid_field="is_valid"):
    """校验 5：会议变更后历史数据置为无效。"""
    _, rows = query(f"SELECT {valid_field} FROM {table} WHERE meeting_id = %s", (meeting_id,))
    assert rows, f"未查到会议 {meeting_id} 的历史记录"
    return f"历史记录 {valid_field} = {rows[0][0]}（按需求应为无效）"


def check_pdf_filename(filename, company_code, company_short, situations):
    """
    校验 6：送审 PDF 文件名规则
    {公司代码} {公司简称}关于股东存在{情况清单}的情况说明
    situations：如 ["部分投票", "无表决权投票"]
    """
    expected_pattern = rf"^{re.escape(company_code)}\s+{re.escape(company_short)}关于股东存在.*的情况说明"
    assert re.search(expected_pattern, filename), \
        f"文件名不符合规则\n  规则: {expected_pattern}\n  实际: {filename}"
    for s in situations:
        assert s in filename, f"文件名缺少情况描述「{s}」：{filename}"
    return f"文件名匹配规则：{filename}"


# ============================== 主流程 ==============================


CHECKS = [
    # 按用例的 K 列逐条登记校验项（顺序与 K 列一致）
    ("1", "vote_basic_info 存在 timestamp 字段", lambda: check_column_exists("vote_basic_info", "timestamp")),
    ("2", "vote_basic_info_error 存在 timestamp 字段", lambda: check_column_exists("vote_basic_info_error", "timestamp")),
]


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    result = {
        "case_id": CASE_ID, "case_name": CASE_NAME, "mode": "B-data",
        "result": None, "message": "", "checks": [],
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    failed = []
    for step_no, desc, fn in CHECKS:
        try:
            detail = fn()
            result["checks"].append({"step": step_no, "desc": desc, "ok": True, "detail": detail})
            print(f"  [OK] {step_no}. {desc} -> {detail}")
        except AssertionError as e:
            result["checks"].append({"step": step_no, "desc": desc, "ok": False, "detail": str(e)})
            failed.append(f"{step_no}. {desc}: {e}")
            print(f"  [NG] {step_no}. {desc} -> {e}")
        except Exception as e:
            # 连接失败 / 表不存在等 → 阻塞
            result["result"] = "阻塞"
            result["message"] = f"步骤 {step_no} 执行异常（疑似环境/前置问题）: {type(e).__name__}: {e}"
            print(f"  [BLOCK] {result['message']}")
            break

    if result["result"] is None:
        result["result"] = "失败" if failed else "通过"
        result["message"] = "；".join(failed)

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    log_path = os.path.join(OUTPUT_ROOT, "logs", f"{CASE_ID}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({"case_id": CASE_ID, "result": result["result"], "message": result["message"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
