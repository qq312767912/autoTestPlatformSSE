#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单条 UI 用例脚本模板 —— 上证e投票平台测试 skill（Step4 · A 类）

用法：
    1. 复制本文件为 script/TC-{用例编号}.py
    2. 修改 CONFIG 区（用例编号、用例名称、起始 URL）
    3. 在 run() 中按 test_cases.xlsx 的 J 列步骤逐条实现，按 K 列预期逐条断言
    4. 执行：以 background=true, timeout=300 调用，避免进程树被外层超时杀掉

设计要点（与 skill 规范对应）：
    · 截图命名 screenshots/{用例编号}_step{N}.png，供 Step5 报告关联
    · 断言失败不吞异常：记录失败步骤编号与实际观测值，供回填"实际结果详情"
    · 跳转地址类用例使用 assert_url_exact（含 query 参数完整比对）
    · Vue/React SPA 场景设置 page_load_strategy="none"，见 websiteFeature/{domain}.md
"""

import os
import re
import sys
import time
import json
import traceback
from datetime import datetime

# ============================== CONFIG ==============================

CASE_ID = "TC-XXX"                       # 用例编号（对应 test_cases.xlsx 中 F 列编号前缀）
CASE_NAME = "端-状态-入口，操作，校验点"   # 用例名称（与 F 列一致）
START_URL = "https://vote.test.sseinfo.com/o/home"

OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SHOT_DIR = os.path.join(OUTPUT_ROOT, "screenshots")
LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")

HEADLESS = True
PAGE_LOAD_TIMEOUT = 20
IMPLICIT_WAIT = 3
# SPA（Vue/React）站点请置为 "none"；静态站点用 "normal"
PAGE_LOAD_STRATEGY = "none"

# ============================== 基础设施 ==============================


def init_driver(headless=HEADLESS):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.page_load_strategy = PAGE_LOAD_STRATEGY
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=zh-CN")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    except Exception:
        driver = webdriver.Chrome(service=Service(), options=opts)

    driver.implicitly_wait(IMPLICIT_WAIT)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver, By, WebDriverWait, EC


class CaseRunner:
    """用例执行器：负责截图、步骤记录、断言与结果落盘。"""

    def __init__(self, driver, By, WebDriverWait, EC):
        self.driver = driver
        self.By = By
        self.wait = WebDriverWait(driver, 15)
        self.EC = EC
        self.step_records = []
        self.current_step = 0

    # ---------- 步骤与证据 ----------

    def step(self, desc):
        """声明"我现在执行第 N 步"，用于截图命名与失败定位。"""
        self.current_step += 1
        print(f"  [{self.current_step}] {desc}")
        return self.current_step

    def shoot(self, tag=""):
        """步骤截图，命名 {用例编号}_step{N}[_tag].png"""
        os.makedirs(SHOT_DIR, exist_ok=True)
        name = f"{CASE_ID}_step{self.current_step}"
        if tag:
            name += f"_{tag}"
        path = os.path.join(SHOT_DIR, name + ".png")
        try:
            self.driver.save_screenshot(path)
        except Exception as e:
            print(f"    [!] 截图失败: {e}")
            return ""
        return path

    # ---------- 常用动作 ----------

    def open(self, url, settle=2):
        """带重试的导航 + 稳定等待。"""
        for attempt in range(3):
            try:
                self.driver.get(url)
                break
            except Exception as e:
                print(f"    [!] 第{attempt + 1}次加载失败: {e}")
                time.sleep(2)
        else:
            raise AssertionError(f"页面加载失败: {url}")
        self.wait_dom_ready()
        time.sleep(settle)      # 等异步渲染

    def wait_dom_ready(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def click(self, css=None, xpath=None, text=None):
        """按 css / xpath / 可见文本点击，返回元素。"""
        for _ in range(3):
            try:
                if css:
                    el = self.driver.find_element(self.By.CSS_SELECTOR, css)
                elif xpath:
                    el = self.driver.find_element(self.By.XPATH, xpath)
                else:
                    el = self.driver.find_element(
                        self.By.XPATH, f"//*[contains(normalize-space(text()),'{text}')]")
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                el.click()
                time.sleep(1)
                return el
            except Exception as e:
                last = e
                time.sleep(0.6)
        raise AssertionError(f"元素点击失败 css={css} xpath={xpath} text={text}: {last}")

    # ---------- 断言（失败即抛 AssertionError，携带步骤信息） ----------

    def assert_text_present(self, text, css=None):
        """断言页面上出现指定文本（可限定容器）。"""
        try:
            if css:
                actual = self.driver.find_element(self.By.CSS_SELECTOR, css).text
            else:
                actual = self.driver.find_element(self.By.TAG_NAME, "body").text
        except Exception as e:
            raise AssertionError(f"取值失败: {e}")
        if text not in actual:
            raise AssertionError(f"未找到预期文本「{text}」；实际片段：{actual[:200]!r}")
        return True

    def assert_url_exact(self, expected_url):
        """跳转地址类：完整比对（含 query 参数），不做包含判断。"""
        actual = self.driver.current_url
        if actual.rstrip("/") != expected_url.rstrip("/"):
            raise AssertionError(f"跳转地址不符\n  预期: {expected_url}\n  实际: {actual}")
        return True

    def assert_url_contains(self, fragment):
        actual = self.driver.current_url
        if fragment not in actual:
            raise AssertionError(f"地址中不含「{fragment}」；实际: {actual}")
        return True

    def assert_element_state(self, css, enabled=None, visible=None):
        """断言元素可用性/可见性（用于按钮置灰、返回按钮显隐类验证）。"""
        try:
            el = self.driver.find_element(self.By.CSS_SELECTOR, css)
        except Exception as e:
            raise AssertionError(f"元素不存在 css={css}: {e}")
        if enabled is not None:
            is_enabled = el.is_enabled() and el.get_attribute("disabled") is None \
                and "disabled" not in (el.get_attribute("class") or "")
            if is_enabled != enabled:
                raise AssertionError(f"元素可用状态不符：预期 enabled={enabled}，实际 {is_enabled}")
        if visible is not None:
            if el.is_displayed() != visible:
                raise AssertionError(f"元素可见性不符：预期 visible={visible}，实际 {el.is_displayed()}")
        return True

    def assert_filename_pattern(self, filename, pattern):
        """生成物命名类：按规则库的正则断言文件名。"""
        if not re.search(pattern, filename):
            raise AssertionError(f"文件名不符合规则\n  规则: {pattern}\n  实际: {filename}")
        return True


# ============================== 用例实现 ==============================


def run(r: CaseRunner):
    """
    按 test_cases.xlsx 的 J 列步骤逐条实现；每条步骤对应一个 step()，
    并在其后按 K 列该步骤的预期做断言。
    """

    # --- 步骤 1 ---
    r.step("打开投票首页")
    r.open(START_URL)
    r.shoot("首页")
    # 对应预期 1：页面正常加载，首页元素可见
    r.assert_element_state("body", visible=True)

    # --- 步骤 2 ---
    r.step("点击「回到旧版」按钮")
    r.click(xpath="//*[contains(normalize-space(text()),'回到旧版')]")
    r.shoot("点击后")
    # 对应预期 2：跳转地址完整一致（路径切换类必须精确比对）
    r.assert_url_exact("https://vote.test.sseinfo.com/o/home")

    # --- 步骤 3：示例·按钮置灰验证 ---
    # r.step("不勾选确认复选框，检查「确认提交」按钮状态")
    # r.shoot("未勾选")
    # r.assert_element_state("#confirmSubmit", enabled=False)

    # 🔻 在此继续追加步骤与断言，保持 J/K 一一对应 🔻


# ============================== 主流程 ==============================

def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{CASE_ID}.log")
    result = {
        "case_id": CASE_ID, "case_name": CASE_NAME, "mode": "A-ui",
        "result": None, "failed_step": None, "message": "",
        "screenshots": [], "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    driver = None
    try:
        driver, By, WebDriverWait, EC = init_driver()
        runner = CaseRunner(driver, By, WebDriverWait, EC)
        run(runner)
        result["result"] = "通过"
        print(f"[PASS] {CASE_ID} {CASE_NAME}")
    except AssertionError as e:
        # 断言类失败 —— 属"失败"，需在报告中给失败步骤与实际观测
        result["result"] = "失败"
        result["failed_step"] = "见 message"
        result["message"] = str(e)
        print(f"[FAIL] {CASE_ID}: {e}")
        if driver:
            os.makedirs(SHOT_DIR, exist_ok=True)
            p = os.path.join(SHOT_DIR, f"{CASE_ID}_failed.png")
            try:
                driver.save_screenshot(p)
                result["screenshots"].append(p)
            except Exception:
                pass
    except Exception as e:
        # 环境/前置类异常 —— 属"阻塞"，不要计为失败
        result["result"] = "阻塞"
        result["message"] = f"{type(e).__name__}: {e}"
        print(f"[BLOCK] {CASE_ID}: {result['message']}")
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))

    sys.exit(0 if result["result"] == "通过" else 1)


if __name__ == "__main__":
    main()
