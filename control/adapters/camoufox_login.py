"""开一个可见的 Camoufox 窗口，供人工登录蝉选；登录态保存在独立 profile。

  python camoufox_login.py

窗口会一直开着（最长 20 分钟），登录成功后脚本会自动检测并打印结果。
profile 目录：D:\\start\\ds-harness\\work\\_camoufox-profile
"""
import time
from pathlib import Path

from camoufox.sync_api import Camoufox

PROFILE = r"D:\start\ds-harness\work\_camoufox-profile"
URL = "https://www.chanxuan.com/library/"
Path(PROFILE).mkdir(parents=True, exist_ok=True)


def logged_in(page) -> bool:
    try:
        body = page.inner_text("body")
    except Exception:
        return False
    # 未登录：页面会出现「密码登录 / 短信登录」；已登录则出现「我的」等
    has_login_form = ("密码登录" in body) or ("短信登录" in body)
    return not has_login_form


with Camoufox(headless=False, persistent_context=True, user_data_dir=PROFILE,
              humanize=True, os="windows", locale="zh-CN") as browser:
    # persistent_context 时返回的是 BrowserContext
    page = browser.pages[0] if getattr(browser, "pages", None) else browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    print("[info] 窗口已打开，请扫码/短信登录蝉选", flush=True)

    ok = False
    for i in range(120):  # 最多 20 分钟
        time.sleep(10)
        try:
            if logged_in(page):
                ok = True
                print(f"[ok] 检测到已登录（第 {(i+1)*10} 秒）", flush=True)
                break
        except Exception as exc:  # noqa: BLE001
            print("  检测异常:", type(exc).__name__, flush=True)
        if i % 6 == 5:
            print(f"  [等待] 已等 {(i+1)*10} 秒，仍未登录", flush=True)

    if ok:
        try:
            print("[info] 当前页面标题:", page.title()[:60], flush=True)
            html = page.content()
            import re
            n = len(set(re.findall(r'/goods/([A-Za-z0-9_\-+=]+)', html)))
            print(f"[info] 页面上商品链接: {n} 个", flush=True)
        except Exception as exc:  # noqa: BLE001
            print("  统计失败:", type(exc).__name__, flush=True)
        print("[done] 登录态已写入 profile，窗口保持 2 分钟后关闭", flush=True)
        time.sleep(120)
    else:
        print("[timeout] 未检测到登录，窗口关闭", flush=True)
