import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from threading import Event, Thread
from time import sleep
from playwright.sync_api import sync_playwright, expect

def verify_topology_canvas(page, screenshots):
    viewport = page.get_by_role("region", name="工艺拓扑画布")
    nodes = viewport.get_by_role("button")
    body = page.locator(".result-body")
    outer_scroll = body.evaluate("element => [element.scrollLeft, element.scrollTop]")
    detail = page.get_by_role("complementary", name="拓扑节点详情")

    def card(number):
        return viewport.locator(f'[data-operation="operation:{number}"]')

    def paths():
        return viewport.locator("svg path[marker-end]").evaluate_all("items => items.map(item => item.getAttribute('d'))")

    def position(item):
        return item.evaluate("element => [element.offsetLeft, element.offsetTop]")

    def wheel_to(item):
        bounds = viewport.bounding_box()
        target = item.bounding_box()
        center_y = bounds["y"] + bounds["height"] / 2
        page.mouse.move(bounds["x"] + 12, center_y)
        page.mouse.wheel(0, round(target["y"] + target["height"] / 2 - center_y))
        expect(item).to_be_in_viewport(ratio=0.99)
        page.wait_for_function("([node, center]) => Math.abs(node.getBoundingClientRect().y + node.offsetHeight / 2 - center) <= 1",
                               arg=[item.element_handle(), center_y])

    expect(detail).to_be_hidden()
    first = position(card("01"))
    for number in ("01", "02", "03", "04"):
        expect(card(number)).to_be_in_viewport(ratio=0.99)
        x, y = position(card(number))
        assert x == first[0] and y == first[1] + (int(number) - 1) * (position(card("02"))[1] - first[1])
    assert position(card("Z1"))[1] == position(card("04"))[1]
    assert position(card("Z1"))[0] < first[0]
    side_positions = [position(card(number)) for number in ("Z2", "Z3")]
    assert all(y == position(card("07"))[1] for _, y in side_positions)
    assert side_positions[0][0] < first[0] < side_positions[1][0]
    for side, target in (("Z1", "05"), ("Z2", "08"), ("Z3", "08")):
        edge = viewport.locator(f'path[data-source="operation:{side}"][data-target="operation:{target}"]')
        assert " C " in edge.get_attribute("d")
    if screenshots:
        page.screenshot(path=screenshots / "topology-start.png")

    wheel_to(card("08"))
    for number in ("Z2", "Z3", "05"):
        expect(card(number)).to_be_in_viewport(ratio=0.95)
    merge_y = card("08").bounding_box()["y"]
    card("08").click()
    expect(detail.get_by_role("heading", name="08 · 模组堆叠总装")).to_be_visible()
    expect(card("08")).to_be_in_viewport(ratio=0.99)
    assert abs(card("08").bounding_box()["y"] - merge_y) < 2
    assert detail.bounding_box()["x"] >= viewport.bounding_box()["x"] + viewport.bounding_box()["width"]
    detail.get_by_role("tab", name="原文证据").click()
    expect(detail.get_by_role("button", name="工艺需求文件.xlsx").first).to_be_visible()
    if screenshots:
        page.screenshot(path=screenshots / "topology-evidence.png")
    detail.get_by_role("tab", name="关联事实").click()
    expect(detail.locator("summary").first).to_be_visible()
    detail.get_by_role("button", name="收起节点详情").click()
    expect(detail).to_be_hidden()

    search = page.get_by_role("combobox", name="搜索工序")
    search.fill("05")
    page.get_by_text("05 · 单体装配", exact=True).click()
    expect(card("05")).to_have_attribute("data-located", "true")
    for number in ("04", "05", "06", "Z1"):
        expect(card(number)).to_be_in_viewport(ratio=0.95)
    assert nodes.count() == 16
    page.get_by_role("button", name="回到起点").click()
    expect(card("01")).to_be_in_viewport(ratio=0.99)

    page.get_by_role("button", name="放大").click()
    expect(page.get_by_text("120%", exact=True)).to_be_visible()
    bounds = viewport.bounding_box()
    assert body.evaluate("element => element.scrollWidth === element.clientWidth")

    world = viewport.locator(".topology-world")
    def camera():
        return world.evaluate("element => { const m = new DOMMatrix(element.style.transform); return [m.e, m.f]; }")

    def expect_horizontal_offset(offset):
        page.wait_for_function("x => Math.abs(new DOMMatrix(document.querySelector('.topology-world').style.transform).e - x) < 0.1",
                               arg=offset)

    x, y = camera()
    background = (bounds["x"] + 25, bounds["y"] + 45)
    page.mouse.move(*background)
    page.mouse.down()
    page.mouse.move(background[0] + 60, background[1] - 30, steps=5)
    page.mouse.up()
    panned = camera()
    assert all(abs(actual - expected) < 0.1 for actual, expected in zip(panned, [x + 60, y - 30])), panned

    page.keyboard.down("Shift")
    page.mouse.wheel(0, 120)
    page.keyboard.up("Shift")
    expect_horizontal_offset(x - 60)
    page.mouse.wheel(-60, 0)
    expect_horizontal_offset(x)
    assert body.evaluate("element => [element.scrollLeft, element.scrollTop]") == outer_scroll

    node = card("03")
    expect(node).to_be_in_viewport(ratio=0.99)
    node_box = node.bounding_box()
    def positions():
        return nodes.evaluate_all("items => items.map(item => [item.title, item.offsetLeft, item.offsetTop])")

    original_positions, original_paths = positions(), paths()
    start_x, start_y = node_box["x"] + node_box["width"] / 2, node_box["y"] + node_box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 36, start_y + 48, steps=6)
    during_drag = paths()
    assert 0 < sum(before != after for before, after in zip(original_paths, during_drag)) < len(original_paths)
    page.mouse.up()
    assert paths() == during_drag
    expected_positions = [[name, left + 30, top + 40] if name == "贴胶" else [name, left, top]
                          for name, left, top in original_positions]
    assert positions() == expected_positions
    expect(detail).to_be_hidden()
    expect(node).to_have_attribute("aria-pressed", "false")
    node.click()
    expect(detail.get_by_role("heading", name="03 · 贴胶", exact=True)).to_be_visible()
    detail.get_by_role("button", name="收起节点详情").click()

    page.get_by_role("button", name="适应画布").click()
    for card in nodes.all():
        expect(card).to_be_in_viewport(ratio=0.99)
    page.get_by_role("tab", name="概览").click()
    page.get_by_role("tab", name="工艺拓扑").click()
    assert positions() == expected_positions


def verify_semantic_workspace(page, screenshots, compact=False):
    page.get_by_role("tab", name="语义模型").click()
    page.get_by_role("button", name="实体列表", exact=True).click()
    directory = page.get_by_role("complementary", name="语义实体列表")
    search = directory.get_by_role("textbox", name="搜索实体")
    search.fill("FEED")
    directory.get_by_label("实体类型", exact=True).select_option("capabilities")
    directory.get_by_label("实体校验状态").select_option("verified")
    feed_row = directory.get_by_role("button", name="供料")
    feed_row.click()
    detail = page.get_by_role("complementary", name="语义详情")
    expect(detail.get_by_role("heading", name="供料", exact=True)).to_be_visible()
    page.get_by_role("button", name="查看关系", exact=True).click()

    graph = page.get_by_role("region", name="语义关系图")
    center = graph.locator("[data-center=true]")
    operation = graph.locator('[data-entity="operation:01"]')
    relation = graph.get_by_role("button", name=re.compile(r"^01 · .* → 需要能力 → 供料"))
    expect(center).to_have_text("制造能力供料")
    expect(operation).to_be_in_viewport(ratio=0.95)
    expect(relation).to_have_attribute("data-status", "verified")
    expect(relation.locator(".semantic-edge-stroke")).to_have_css("stroke-dasharray", "none")
    assert graph.locator("button[data-entity]").count() == 5
    if compact:
        assert detail.bounding_box()["y"] >= graph.bounding_box()["y"] + graph.bounding_box()["height"]
        assert graph.bounding_box()["width"] == detail.bounding_box()["width"]

    relation.locator("text").click()
    expect(detail.get_by_role("heading", name="需要能力 → 供料")).to_be_visible()
    detail.get_by_role("tab", name="原文证据").click()
    expect(detail.get_by_text("Sheet1 / C2", exact=True)).to_be_visible()
    expect(center).to_be_in_viewport(ratio=0.95)
    if screenshots:
        page.screenshot(path=screenshots / ("semantics-mobile.png" if compact else "semantics-evidence.png"))

    if not compact:
        detail.get_by_role("button", name="收起关系详情").click()
        graph_box = graph.bounding_box()
        world = graph.locator(".semantic-world")
        start = world.get_attribute("style")
        page.mouse.move(graph_box["x"] + 58, graph_box["y"] + 54)
        page.mouse.wheel(0, 35)
        expect(world).not_to_have_attribute("style", start)
        page.get_by_role("button", name="回到焦点").click()
        node_box = operation.bounding_box()
        path_before = relation.locator(".semantic-edge-stroke").get_attribute("d")
        x, y = node_box["x"] + node_box["width"] / 2, node_box["y"] + node_box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 27, y + 13, steps=5)
        page.mouse.up()
        expect(relation.locator(".semantic-edge-stroke")).not_to_have_attribute("d", path_before)
        expect(detail).to_be_hidden()
        operation.click()
        detail.get_by_role("button", name="以此为中心").click()
        expect(center).to_have_attribute("data-entity", "operation:01")
        page.get_by_role("button", name="返回上一个焦点").click()
        expect(center).to_have_text("制造能力供料")
        page.get_by_role("button", name="全图", exact=True).click()
        page.get_by_label("关系图实体类型").select_option("all")
        assert graph.locator("button[data-entity]").count() > 5
        page.get_by_role("button", name="返回焦点", exact=True).click()
        expect(center).to_have_text("制造能力供料")

    page.get_by_role("button", name="实体列表", exact=True).click()
    directory.get_by_label("实体类型", exact=True).select_option("capabilities")
    expect(feed_row).to_have_attribute("aria-pressed", "true")
    expect(search).to_have_value("FEED")
    assert page.locator(".result-body").evaluate("el => el.scrollWidth === el.clientWidth && el.scrollTop === 0")


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
        page.get_by_role("button", name="开始解析").click()
        assert entered.wait(5), "Provider did not receive the running request"
        expect(page.get_by_text("分批提取事实 batch-1 · 进行中", exact=True)).to_be_visible(timeout=1500)
        with page.expect_response(lambda response: response.url.endswith("/cancel")) as cancel:
            page.get_by_role("button", name="中止").click()
        assert cancel.value.status == 202
        expect(page.get_by_role("button", name="开始解析")).to_be_visible()
        # Let the local provider finish so the assertion also detects orphaned runs.
        sleep(2.5)
        persisted = page.request.get(session_url).json()
        assert persisted["messages"] == [] and persisted["current_extracted_json"] is None
        page.close()



def verify_mixed_overview(browser, base_url, workbook, screenshots):
    narrative = workbook.with_name("自然语言建模需求.md")
    if not narrative.is_file():
        return
    page = browser.new_page(viewport={"width": 1512, "height": 982}, timezone_id="Asia/Shanghai")
    session = page.request.post(base_url + "/api/sessions", data={}).json()
    url = f"{base_url}/api/sessions/{session['id']}"
    files = page.request.post(url + "/files", multipart={"files": {
        "name": workbook.name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "buffer": workbook.read_bytes(),
    }}).json()
    run = page.request.post(url + "/run", data={
        "message": narrative.read_text(), "file_ids": [files[0]["id"]], "use_demo_fallback": True,
    })
    assert run.ok, run.text()
    page.goto(base_url)
    page.get_by_role("button", name="打开语义结果 →").last.click()
    amount = page.get_by_role("button", name=re.compile(r"方形电芯.*8 个"))
    limit = page.get_by_role("button", name=re.compile(r"≤ 120 s \/ 模组"))
    expect(amount).to_be_visible()
    expect(limit).to_be_visible()
    if screenshots:
        page.screenshot(path=screenshots / "mixed-overview.png")
    limit.click()
    detail = page.get_by_role("complementary", name="语义详情")
    detail.get_by_role("tab", name="原文证据").click()
    expect(detail.get_by_text(re.compile(r"设计下线节拍不超过120"))).to_be_visible()
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
    page.get_by_role("button", name="开始解析").click()
    expect(page.get_by_role("button", name="打开语义结果 →").last).to_be_visible(timeout=12000)
    expect(page.get_by_role("heading", name="制造需求概览")).to_be_visible()
    if args.screenshots:
        page.screenshot(path=args.screenshots / "desktop.png")
    page.get_by_role("tab", name="校验问题").click()
    expect(page.get_by_text("原文给出不合格产出，后续处理路径待确认", exact=True)).to_be_visible()
    page.get_by_role("button", name="定位事实").first.click()
    expect(page.get_by_role("complementary", name="语义详情")).to_be_visible()
    page.get_by_role("tab", name="工艺拓扑").click()
    verify_topology_canvas(page, args.screenshots)
    with page.expect_download() as graph_export:
        page.get_by_role("button", name="导出图 JSON").click()
    assert graph_export.value.suggested_filename.startswith("process-graph-")
    page.get_by_role("tab", name="语义模型").click()
    page.get_by_role("button", name="浏览本体").click()
    expect(page.get_by_role("dialog", name="领域本体 3.0.0")).to_be_visible()
    page.get_by_role("tab", name="对象关系").click()
    page.get_by_placeholder("搜索类、关系或属性").fill("directlyPrecedes")
    expect(page.get_by_text("directlyPrecedes", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    verify_semantic_workspace(page, args.screenshots)
    page.get_by_role("tab", name="结构化数据").click()
    with page.expect_download() as download:
        page.get_by_role("button", name="JSON", exact=True).click()
    assert download.value.suggested_filename.endswith(".json")
    page.get_by_role("button", name="更多结果视图").click()
    page.get_by_role("menuitem", name="模板预览").click()
    page.get_by_label("输出模板", exact=True).click()
    page.get_by_role("option", name="产线节拍与物料汇总表", exact=False).click()
    expect(page.get_by_role("columnheader", name="单件工时", exact=False)).to_be_visible()
    expect(page.get_by_text("原文未提供", exact=True).first).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="XLSX", exact=True).click()
    assert download.value.suggested_filename.endswith(".xlsx")
    page.get_by_role("button", name="更多结果视图").click()
    page.get_by_role("menuitem", name="Debug").click()
    expect(page.get_by_text("输入文件解析", exact=True)).to_be_visible()
    expect(page.get_by_text("确定性抽取", exact=True)).to_be_visible()
    page.get_by_role("tab", name="概览").click()
    page.get_by_role("button", name="基于此版本修订").click()
    page.get_by_label("输入工艺需求").fill("把第三道工序的工时修改为45秒。")
    page.get_by_role("button", name="开始解析").click()
    expect(page.get_by_label("输入工艺需求")).to_be_enabled()
    expect(page.get_by_text("已生成语义版本。", exact=False)).to_have_count(2, timeout=12000)
    page.get_by_role("button", name="更多结果视图").click()
    page.get_by_role("menuitem", name="模板预览").click()
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
        expect(page.get_by_role("heading", name="解析结果")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth"), width
        assert page.evaluate("document.querySelector('main').scrollWidth <= innerWidth"), width
        if width == 390:
            if args.screenshots:
                page.screenshot(path=args.screenshots / "mobile.png")
            page.get_by_role("tab", name="工艺拓扑").click()
            canvas = page.get_by_role("region", name="工艺拓扑画布")
            canvas_width = canvas.bounding_box()["width"]
            first = canvas.get_by_role("button").first
            first.click()
            detail = page.get_by_role("complementary", name="拓扑节点详情")
            expect(detail).to_be_in_viewport(ratio=0.99)
            expect(first).to_be_in_viewport(ratio=0.99)
            assert canvas.bounding_box()["width"] == canvas_width
            assert detail.bounding_box()["y"] > canvas.bounding_box()["y"] + canvas.bounding_box()["height"]
            detail.get_by_role("tab", name="原文证据").click()
            expect(detail.get_by_role("button", name="工艺需求文件.xlsx").first).to_be_visible()
            detail.get_by_role("tab", name="关联事实").click()
            facts = detail.get_by_role("tabpanel", name="关联事实")
            header = detail.get_by_role("heading").first.bounding_box()
            camera = canvas.locator(".topology-world").get_attribute("style")
            facts.hover()
            page.mouse.wheel(0, 280)
            page.wait_for_function("element => element.scrollTop > 0", arg=facts.element_handle(), timeout=3000)
            assert detail.get_by_role("heading").first.bounding_box() == header
            assert canvas.locator(".topology-world").get_attribute("style") == camera
            if args.screenshots:
                page.screenshot(path=args.screenshots / "topology-mobile.png")
            detail.get_by_role("button", name="收起节点详情").click()
            expect(detail).to_be_hidden()
            verify_semantic_workspace(page, args.screenshots, compact=True)
            page.get_by_role("button", name="素材与会话", exact=True).click()
            expect(page.get_by_role("textbox", name="搜索历史会话")).to_be_visible()
            page.get_by_role("button", name="需求对话", exact=True).click()
            expect(page.get_by_label("输入工艺需求")).to_be_visible()
    assert not errors, errors
    verify_mixed_overview(browser, args.base_url, workbook, args.screenshots)
    verify_cancel(browser, args.base_url)
    print("Cancellation reached the backend with no partial run persisted.")
    print("Browser regression passed at 1512, 1280, 900, 390 and 320 px.")
