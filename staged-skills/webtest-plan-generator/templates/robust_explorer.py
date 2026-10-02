#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用健壮页面探索脚本 v2.0（模板版）
适用任何网站的页面结构探索，支持 headless 模式、多级超时防护、Stale element 重试。

用法：
    python robust_explorer.py --url https://example.com --output ./reports/example --headless

参数：
    --url         目标网站 URL（必需）
    --output      输出目录（默认 ./explore_results）
    --headless    是否启用无头模式（默认 True）
    --paths       额外探索的子路径，JSON 数组格式（可选，如 '["/about","/contact"]'）
    --timeout     页面加载超时（秒，默认 20）
    --retries     重试次数（默认 2）

输出：
    在输出目录下生成：
        explore_results_{timestamp}.json   # 结构化探索结果
        explore_log_{timestamp}.txt        # 执行日志
        截图文件（若截图成功）
"""

import os
import sys
import json
import time
import argparse
import traceback
from datetime import datetime

# ===================== 核心函数（无需修改，可直接复用） =====================

def init_driver(headless=True, timeout=20):
    """初始化浏览器驱动，返回 driver, By, WebDriverWait, EC。"""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-web-security")
    opts.add_argument("--lang=zh-CN")
    # 禁用图片加载（可加速，若需要图片可注释）
    prefs = {"profile.managed_default_content_settings.images": 2}
    opts.add_experimental_option("prefs", prefs)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception:
        # 降级：尝试使用系统 PATH 中的 chromedriver
        service = Service()
        driver = webdriver.Chrome(service=service, options=opts)

    driver.implicitly_wait(3)
    driver.set_page_load_timeout(timeout)
    return driver, By, WebDriverWait, EC


def safe_get(driver, url, retries=2, delay=3):
    """带重试的页面导航，防止加载失败。"""
    for i in range(retries):
        try:
            driver.get(url)
            time.sleep(2)  # 等待页面初始渲染
            return True
        except Exception as e:
            print(f"  [!] 第{i+1}次加载失败: {e}")
            if i < retries - 1:
                time.sleep(delay)
    return False


def safe_find_all(driver, By, locator, retries=2):
    """Stale element 自动重试的 find_elements。"""
    for _ in range(retries):
        try:
            return driver.find_elements(*locator)
        except Exception:
            time.sleep(0.5)
    return []


def save_screenshot(driver, output_base, name):
    """保存截图，返回路径。"""
    os.makedirs(output_base, exist_ok=True)
    path = os.path.join(output_base, f"{name}.png")
    try:
        driver.save_screenshot(path)
        return path
    except Exception as e:
        print(f"  [!] 截图失败 {name}: {e}")
        return ""


def extract_page_info(driver, By):
    """
    提取当前页面的结构化信息。
    注意：By 必须作为参数传入，避免 NameError。
    """
    info = {
        "url": driver.current_url,
        "title": driver.title,
        "meta_description": "",
        "h1_tags": [],
        "main_nav_links": [],
        "forms": [],
        "interactive_buttons": [],
        "input_fields": [],
        "tables": [],
        "images": [],
        "iframes": [],
        "all_links": [],
    }

    # Meta description
    try:
        desc_elem = driver.find_elements(By.CSS_SELECTOR, 'meta[name="description"]')
        if desc_elem:
            info["meta_description"] = desc_elem[0].get_attribute("content") or ""
    except:
        pass

    # H1
    for h1 in safe_find_all(driver, By, (By.CSS_SELECTOR, "h1")):
        text = h1.text.strip()
        if text:
            info["h1_tags"].append(text)

    # 导航链接（常见选择器组合）
    nav_links = safe_find_all(driver, By, (By.CSS_SELECTOR, "nav a, .nav a, .menu a, header a"))
    for a in nav_links:
        try:
            href = a.get_attribute("href") or ""
            text = a.text.strip()
            if href and text:
                info["main_nav_links"].append({"text": text, "href": href})
        except:
            pass

    # 所有链接
    for a in safe_find_all(driver, By, (By.TAG_NAME, "a")):
        try:
            href = a.get_attribute("href") or ""
            text = a.text.strip()[:50]
            if href:
                info["all_links"].append({"text": text, "href": href})
        except:
            pass

    # 表单
    for form in safe_find_all(driver, By, (By.TAG_NAME, "form")):
        form_info = {
            "action": form.get_attribute("action") or "",
            "method": form.get_attribute("method") or "get",
            "inputs": []
        }
        for inp in form.find_elements(By.TAG_NAME, "input"):
            form_info["inputs"].append({
                "name": inp.get_attribute("name") or "",
                "type": inp.get_attribute("type") or "text",
                "id": inp.get_attribute("id") or "",
                "placeholder": inp.get_attribute("placeholder") or ""
            })
        info["forms"].append(form_info)

    # 按钮
    btn_selectors = "button, .btn, input[type='submit'], input[type='button'], a[class*='btn']"
    for btn in safe_find_all(driver, By, (By.CSS_SELECTOR, btn_selectors)):
        try:
            text = btn.text.strip() or btn.get_attribute("value") or ""
            if not text:
                text = btn.get_attribute("class") or ""
            if text:
                info["interactive_buttons"].append(text[:80])
        except:
            pass

    # 输入框
    for inp in safe_find_all(driver, By, (By.CSS_SELECTOR, "input:not([type='hidden']), textarea, select")):
        try:
            name = inp.get_attribute("name") or ""
            inp_type = inp.get_attribute("type") or "text"
            placeholder = inp.get_attribute("placeholder") or ""
            if name or placeholder:
                info["input_fields"].append({
                    "name": name,
                    "type": inp_type,
                    "placeholder": placeholder
                })
        except:
            pass

    # 表格
    for table in safe_find_all(driver, By, (By.TAG_NAME, "table")):
        rows = len(table.find_elements(By.TAG_NAME, "tr"))
        cols = len(table.find_elements(By.CSS_SELECTOR, "tr:first-child td, tr:first-child th"))
        info["tables"].append({"rows": rows, "cols": cols})

    # 图片
    for img in safe_find_all(driver, By, (By.TAG_NAME, "img")):
        try:
            src = img.get_attribute("src") or ""
            alt = img.get_attribute("alt") or ""
            if src:
                info["images"].append({"src": src[:200], "alt": alt[:50]})
        except:
            pass

    # iframe
    for iframe in safe_find_all(driver, By, (By.TAG_NAME, "iframe")):
        try:
            src = iframe.get_attribute("src") or ""
            name = iframe.get_attribute("name") or ""
            info["iframes"].append({"src": src[:200], "name": name})
        except:
            pass

    return info


def explore_static_elements(driver, By, output_base, ts):
    """
    主页及静态子页面探索（根据给定的额外路径列表）。
    返回 results 列表。
    """
    results = []
    # 主页
    print("[*] 探索主页...")
    page_info = {"step": "homepage", "url": driver.current_url}
    page_info.update(extract_page_info(driver, By))
    page_info["screenshot"] = save_screenshot(driver, output_base, f"01_homepage_{ts}")
    # 记录链接样本
    page_info["sub_links_count"] = len(page_info["all_links"])
    page_info["first_10_links"] = [l for l in page_info["all_links"] if l["href"].startswith("http")][:10]
    results.append(page_info)

    return results


def explore_dynamic_elements(driver, By, output_base):
    """
    分析页面中的动态数据属性、内嵌 JSON 等。
    """
    dynamic_info = {
        "step": "dynamic_analysis",
        "ajax_loaders": [],
        "json_endpoints": [],
    }
    # 数据属性
    for elem in safe_find_all(driver, By, (By.CSS_SELECTOR, "[data-url], [data-src], [data-href]")):
        try:
            data_url = (elem.get_attribute("data-url") or
                        elem.get_attribute("data-src") or
                        elem.get_attribute("data-href"))
            if data_url:
                dynamic_info["ajax_loaders"].append({
                    "tag": elem.tag_name,
                    "cls": (elem.get_attribute("class") or "")[:50],
                    "data": data_url[:200]
                })
        except:
            pass

    # 内嵌 JSON
    for s in safe_find_all(driver, By, (By.CSS_SELECTOR, "script[type='application/json'], script[type='text/json']")):
        try:
            text = s.get_attribute("innerHTML") or ""
            if text and len(text) < 2000:
                dynamic_info["json_endpoints"].append(text[:500])
        except:
            pass

    return dynamic_info if (dynamic_info["ajax_loaders"] or dynamic_info["json_endpoints"]) else None


def explore_extra_paths(driver, By, output_base, extra_paths, ts):
    """
    探索额外的子路径（如 /about, /contact）。
    返回新发现的页面信息列表。
    """
    results = []
    base_url = driver.current_url.rstrip('/')
    for path in extra_paths:
        full_url = base_url + path
        print(f"[*] 尝试额外路径: {full_url}")
        if safe_get(driver, full_url):
            # 简单判断页面是否有效（有标题或主要内容）
            if driver.title and not driver.title.startswith("Error"):
                info = extract_page_info(driver, By)
                info["step"] = f"extra_{path.strip('/').replace('/', '_')}"
                info["url"] = full_url
                info["screenshot"] = save_screenshot(driver, output_base, f"extra_{ts}_{path.strip('/')}")
                results.append(info)
                # 为了避免过多请求，只探索前5个成功的路径
                if len(results) >= 5:
                    break
    return results


# ===================== 主函数 =====================

def main():
    parser = argparse.ArgumentParser(description="通用健壮页面探索脚本 v2.0")
    parser.add_argument("--url", required=True, help="目标网站 URL")
    parser.add_argument("--output", default="./explore_results", help="输出目录")
    parser.add_argument("--headless", action="store_true", default=True, help="启用无头模式")
    parser.add_argument("--paths", default="[]", help="额外探索的子路径，JSON 数组，如 '[\"/about\",\"/contact\"]'")
    parser.add_argument("--timeout", type=int, default=20, help="页面加载超时（秒）")
    parser.add_argument("--retries", type=int, default=2, help="重试次数")
    args = parser.parse_args()

    TARGET_URL = args.url
    OUTPUT_BASE = args.output
    HEADLESS = args.headless
    TIMEOUT = args.timeout
    RETRIES = args.retries
    try:
        extra_paths = json.loads(args.paths)
    except json.JSONDecodeError:
        print("[!] --paths 参数格式错误，应为 JSON 数组，使用默认空列表")
        extra_paths = []

    # 创建输出目录
    os.makedirs(OUTPUT_BASE, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(OUTPUT_BASE, f"explore_log_{ts}.txt")
    log_file = open(log_path, "w", encoding="utf-8")

    def log(msg):
        print(msg)
        log_file.write(msg + "\n")
        log_file.flush()

    driver = None
    try:
        log(f"[*] 初始化浏览器 (headless={HEADLESS}, timeout={TIMEOUT})...")
        driver, By, WebDriverWait, EC = init_driver(HEADLESS, TIMEOUT)
        log("[+] 浏览器启动成功")

        log(f"[*] 加载目标页面: {TARGET_URL}")
        if not safe_get(driver, TARGET_URL, retries=RETRIES):
            log("[!] 主页加载失败，终止探索")
            sys.exit(1)

        # 1. 主页静态信息
        results = explore_static_elements(driver, By, OUTPUT_BASE, ts)

        # 2. 额外路径探索
        if extra_paths:
            extra_results = explore_extra_paths(driver, By, OUTPUT_BASE, extra_paths, ts)
            results.extend(extra_results)

        # 3. 动态元素分析
        dynamic_info = explore_dynamic_elements(driver, By, OUTPUT_BASE)
        if dynamic_info:
            results.append(dynamic_info)

        # 保存结果
        result_path = os.path.join(OUTPUT_BASE, f"explore_results_{ts}.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        log(f"[+] 探索结果已保存: {result_path}")
        log(f"[+] 共探索 {len(results)} 个页面/步骤")
        log("[*] 完成!")

    except Exception as e:
        log(f"[!] 错误: {type(e).__name__}: {e}")
        traceback.print_exc()
        if driver:
            error_screenshot = save_screenshot(driver, OUTPUT_BASE, f"error_{ts}")
            log(f"[!] 错误截图已保存: {error_screenshot}")

    finally:
        if driver:
            driver.quit()
        log_file.close()
        print(f"\n[*] 日志: {log_path}")


if __name__ == "__main__":
    main()