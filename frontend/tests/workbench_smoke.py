import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import sleep
from playwright.sync_api import sync_playwright, expect

def verify_cancel(browser, base_url):
    entered = Event()

    class SlowProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            sleep(2)
            self.send_response(503)
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), SlowProvider) as upstream:
        Thread(target=upstream.serve_forever, daemon=True).start()
        page = browser.new_page()
        agent = page.request.post(base_url + "/api/agents", data={
            "name": "取消测试",
            "base_url": f"http://127.0.0.1:{upstream.server_port}/v1",
            "api_key": "local-placeholder",
            "model": "mock-delay",
        }).json()
        session = page.request.post(base_url + "/api/sessions", data={"agent_id": agent["id"]}).json()
        session_url = base_url + "/api/sessions/" + session["id"]
        page.goto(base_url)
        expect(page.get_by_label("输入工艺需求")).to_be_enabled()
        page.get_by_label("输入工艺需求").fill("测试中止时的记录一致性。")
        page.get_by_role("button", name="发送测试").click()
        assert entered.wait(5), "Provider did not receive the running request"
        expect(page.get_by_text("分批提取事实 batch-1 · 进行中", exact=True)).to_be_visible(timeout=1500)
        with page.expect_response(lambda response: response.url.endswith("/cancel")) as cancel:
            page.get_by_role("button", name="中止").click()
        assert cancel.value.status == 202
        expect(page.get_by_role("button", name="发送测试")).to_be_visible()
        # Let the local provider finish so the assertion also detects orphaned runs.
        sleep(2.5)
        persisted = page.request.get(session_url).json()
        assert persisted["messages"] == [] and persisted["current_extracted_json"] is None
        page.close()


parser = argparse.ArgumentParser(description="Chrome regression against an isolated Assessment instance")
parser.add_argument("--base-url", default="http://127.0.0.1:15173")
parser.add_argument("--workbook", type=Path, required=True)
parser.add_argument("--chrome", help="Path to Chrome. Uses Playwright Chromium when omitted.")
parser.add_argument("--screenshots", type=Path)
args = parser.parse_args()
workbook = args.workbook
if args.screenshots:
    args.screenshots.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=args.chrome)
    page = browser.new_page(viewport={"width": 1512, "height": 982}, accept_downloads=True, timezone_id="Asia/Shanghai")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.base_url)
    expect(page.get_by_role("heading", name="素材与会话")).to_be_visible()
    expect(page.get_by_role("button", name="新建会话")).to_be_enabled()
    expect(page.get_by_label("输入工艺需求")).to_be_enabled()
    with page.expect_response(lambda response: response.url.endswith("/api/sessions") and response.request.method == "POST"):
        page.get_by_role("button", name="新建会话").click()
    expect(page.get_by_label("输入工艺需求")).to_be_enabled()
    page.locator('input[type=file]').set_input_files({"name":"工艺需求文件.xlsx","mimeType":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","buffer":workbook.read_bytes()})
    expect(page.get_by_text("工艺需求文件.xlsx", exact=True).first).to_be_visible()
    page.get_by_role("button", name="发送测试").click()
    expect(page.get_by_role("button", name="打开语义结果 →").last).to_be_visible(timeout=12000)
    expect(page.get_by_role("heading", name="制造需求概览")).to_be_visible()
    if args.screenshots:
        page.screenshot(path=args.screenshots / "desktop.png")
    page.get_by_role("tab", name="工艺拓扑").click()
    expect(page.get_by_role("button", name="01 · 主线工序 电芯上料")).to_be_visible()
    page.get_by_role("button", name="01 · 主线工序 电芯上料").click()
    expect(page.get_by_role("heading", name="原文证据")).to_be_visible()
    page.get_by_role("button", name="放大").click()
    expect(page.get_by_text("120%", exact=True)).to_be_visible()
    with page.expect_download() as graph_export:
        page.get_by_role("button", name="导出图 JSON").click()
    assert graph_export.value.suggested_filename.startswith("process-graph-")
    page.get_by_role("tab", name="语义模型").click()
    page.get_by_role("button", name="浏览本体").click()
    expect(page.get_by_role("dialog", name="领域本体 2.0.0")).to_be_visible()
    page.get_by_role("tab", name="对象关系").click()
    page.get_by_placeholder("搜索类、关系或属性").fill("directlyPrecedes")
    expect(page.get_by_text("directlyPrecedes", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.get_by_role("button", name="关系图").click()
    expect(page.get_by_role("region", name="语义关系图")).to_be_visible()
    page.get_by_role("tab", name="结构化数据").click()
    with page.expect_download() as download:
        page.get_by_role("button", name="JSON", exact=True).click()
    assert download.value.suggested_filename.endswith(".json")
    page.get_by_role("tab", name="模板预览").click()
    page.get_by_label("输出模板", exact=True).click()
    page.get_by_role("option", name="产线节拍与物料汇总表", exact=False).click()
    expect(page.get_by_role("columnheader", name="单件工时", exact=False)).to_be_visible()
    expect(page.get_by_text("原文未提供", exact=True).first).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="XLSX", exact=True).click()
    assert download.value.suggested_filename.endswith(".xlsx")
    page.get_by_role("tab", name="Debug").click()
    expect(page.get_by_text("输入文件解析", exact=True)).to_be_visible()
    expect(page.get_by_text("确定性抽取", exact=True)).to_be_visible()
    page.get_by_role("tab", name="概览").click()
    page.get_by_role("button", name="基于此版本修订").click()
    page.get_by_label("输入工艺需求").fill("把第三道工序的工时修改为45秒。")
    page.get_by_role("button", name="发送测试").click()
    expect(page.get_by_label("输入工艺需求")).to_be_enabled()
    expect(page.get_by_text("已生成语义版本。", exact=False)).to_have_count(2, timeout=12000)
    page.get_by_role("tab", name="模板预览").click()
    expect(page.get_by_role("cell", name="45", exact=True)).to_be_visible()
    page.get_by_role("tab", name="概览").click()
    page.get_by_role("button", name="人工审核").click()
    dialog = page.get_by_role("dialog", name="人工审核 · 保存为新版本")
    expect(dialog).to_be_visible()
    control = dialog.get_by_label("工序", exact=True)
    control.press("ArrowDown")
    control.press("Enter")
    dialog.get_by_label("修正名称").fill("已审核工序")
    dialog.get_by_label("审核依据").fill("现场核对工序名称")
    dialog.get_by_role("button", name="保存审核版本").click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_text("已保存人工审核版本。", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("已生成语义版本。", exact=False)).to_have_count(2)
    for width in (1280, 900, 390, 320):
        page.set_viewport_size({"width": width, "height": 844})
        if width <= 1040:
            page.get_by_role("button", name="解析结果", exact=True).click()
        expect(page.get_by_role("heading", name="结果工作区")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth"), width
        assert page.evaluate("document.querySelector('main').scrollWidth <= innerWidth"), width
        if width == 390:
            if args.screenshots:
                page.screenshot(path=args.screenshots / "mobile.png")
            page.get_by_role("button", name="素材与会话", exact=True).click()
            expect(page.get_by_role("textbox", name="搜索历史会话")).to_be_visible()
            page.get_by_role("button", name="需求对话", exact=True).click()
            expect(page.get_by_label("输入工艺需求")).to_be_visible()
    assert not errors, errors
    verify_cancel(browser, args.base_url)
    print("Cancellation reached the backend with no partial run persisted.")
    print("Browser regression passed at 1512, 1280, 900, 390 and 320 px.")
