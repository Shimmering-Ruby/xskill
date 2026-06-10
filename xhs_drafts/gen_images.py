#!/usr/bin/env python3.11
"""Generate XHS images via apimart.ai doubao-seedream API. All prompts in Chinese."""
import json, os, subprocess, sys, time

API_BASE = "https://api.apimart.ai/v1"
API_KEY = open(os.path.expanduser("~/.apicore_key")).read().strip()
OUT_DIR = os.path.dirname(os.path.abspath(__file__)) + "/images"
os.makedirs(OUT_DIR, exist_ok=True)


def _curl_json(method, url, data=None):
    cmd = ["curl", "-s", "--max-time", "30", url, "-H", f"Authorization: Bearer {API_KEY}"]
    if data:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(r.stdout)


def generate_image(prompt: str, filename: str, size: str = "1024x1536") -> str:
    out_path = os.path.join(OUT_DIR, filename)
    result = _curl_json("POST", f"{API_BASE}/images/generations",
                        {"model": "doubao-seedream-5-0-lite", "prompt": prompt, "n": 1, "size": size})
    task_id = result.get("data", [{}])[0].get("task_id") or result.get("task_id")
    if not task_id:
        raise RuntimeError(f"No task_id: {result}")
    print(f"  [{filename}] submitted {task_id}", flush=True)

    for _ in range(30):
        time.sleep(10)
        poll = _curl_json("GET", f"{API_BASE}/tasks/{task_id}")
        task = poll.get("data", {})
        if task.get("progress", 0) >= 100:
            images = task.get("result", {}).get("images", task.get("result", {}).get("data", []))
            if images:
                url = images[0].get("url", "") if isinstance(images[0], dict) else images[0]
                if isinstance(url, list):
                    url = url[0]
                if url:
                    subprocess.run(["curl", "-s", "-o", out_path, url])
                    print(f"  [{filename}] done -> {out_path}", flush=True)
                    return out_path
            raise RuntimeError(f"No image in result: {task.get('result')}")
        print(f"  [{filename}] polling... {task.get('progress',0)}%", flush=True)
    raise TimeoutError(f"Task {task_id} timed out")


if __name__ == "__main__":
    task_name = sys.argv[1] if len(sys.argv) > 1 else "all"

    TASKS = {
        # === 方案一：干货教学型 ===
        "d1_cover": {
            "prompt": (
                "竖版3:4社交媒体封面图。深蓝色渐变科技感背景。"
                "画面中央有一个发光的DNA双螺旋结构，螺旋上嵌着代码符号「</>」和齿轮图标，象征AI智能体自进化。"
                "上方白色大号粗体中文标题「Coding Agent 也能自进化？」。"
                "下方浅蓝色副标题「调研了10个开源方案告诉你」。"
                "底部有5个小圆形图标横排，用细线彼此相连，代表不同的编程智能体生态。"
                "整体风格：科技感、简洁、信息图风格。"
            ),
            "size": "1024x1536",
        },
        "d1_fig1": {
            "prompt": (
                "扁平插画风格信息图，竖版3:4，白色背景。"
                "画面中一个程序员坐在电脑前，屏幕上显示AI对话框。"
                "程序员头顶有三个重复的对话气泡，分别写着「第1次」「第2次」「第3次」，每个气泡里都在教AI同一件事。"
                "程序员的表情从耐心微笑逐渐变成无奈再到崩溃抓头发。"
                "底部有一个红色横幅，上面白色大字写着「每次对话都是一张白纸」。"
                "风格：扁平设计插画，配色温暖，带点幽默感。"
            ),
            "size": "1024x1536",
        },
        "d1_fig2": {
            "prompt": (
                "数据对比矩阵信息图，竖版3:4，深灰色背景。"
                "顶部白色大字标题「10个轨迹转技能系统横评」。"
                "下方是一个对比表格矩阵，行是功能维度（灰度A/B测试、跨生态支持、数据脱敏、团队模式、无需真实环境），"
                "列是不同系统名称（OpenSpace、Trace2Skill、AutoSkill、EvoSkill、MemSkill、GEPA、xskill）。"
                "用绿色实心圆点●表示支持，灰色空心圆○表示不支持。"
                "最右侧xskill列全部是绿色实心圆点，特别醒目突出。"
                "风格：专业的数据可视化信息图。"
            ),
            "size": "1024x1536",
        },
        "d1_fig3": {
            "prompt": (
                "技术架构流程图，竖版3:4，白色背景。"
                "从上到下的流水线流程："
                "顶部：用户图标加文字「用户轨迹」。"
                "箭头向下到蓝色圆角方框，里面写「TaskAgent — 拆分原子任务（30秒轮询）」。"
                "箭头向下到绿色圆角方框「TaskClusterAgent — 路由+打分（0-10分）」。"
                "箭头向下标注条件「累积≥10分」到橙色圆角方框「SkillEditAgent — 编辑SKILL.md」。"
                "箭头分两路：左边通向绿色标签「main分支（稳定版）」，右边通向黄色标签「staging分支（灰度版）→ A/B对比 → 胜出则上线」。"
                "风格：专业系统架构图，方框有阴影和圆角。"
            ),
            "size": "1024x1536",
        },
        "d1_fig4": {
            "prompt": (
                "数据可视化信息图，正方形1:1，浅色背景。"
                "主题：灰度A/B测试原理。"
                "画面分左右两栏："
                "左栏标题「main分支」，下方文字「80%用户」，画4个简笔人物图标。"
                "右栏标题「staging分支」，下方文字「20%用户」，画1个简笔人物图标。"
                "下方是两根柱状图对比UX体验分，staging柱子略高于main。"
                "staging柱子旁有绿色对勾标注「上线 ✓」。"
                "底部醒目红色大字「基于用户行为信号打分，不是LLM自评」。"
                "风格：简洁清晰的数据图表。"
            ),
            "size": "1024x1024",
        },
        "d1_fig5": {
            "prompt": (
                "模拟macOS终端窗口截图，正方形1:1。"
                "终端窗口有圆角边框，顶部有红黄绿三个小圆点。"
                "黑色背景，绿色等宽字体显示以下命令："
                "$ pip install xskill"
                "$ xskill serve          # 单机模式"
                "$ xskill serve --server  # 团队模式"
                "终端下方白色区域横排列出5个智能体生态名称：Claude Code、Codex、OpenCode、OpenClaw、Cursor。"
                "风格：真实终端截图风格。"
            ),
            "size": "1024x1024",
        },

        # === 方案二：争议观点型 ===
        "d2_cover": {
            "prompt": (
                "竖版3:4社交媒体封面图，纯黑色背景，极简风格。"
                "画面中央两行白色大号粗体中文文字："
                "第一行「你的 Claude Code」"
                "第二行「跟第一天装上时一样蠢。」"
                "两行之间适当留白。"
                "下方一行小号灰色文字「——除非你做了这件事」。"
                "画面除了文字什么都没有，极度简洁，冲击力强。"
            ),
            "size": "1024x1536",
        },
        "d2_fig1": {
            "prompt": (
                "数据对比信息图，竖版3:4，白色背景，分左右两半。"
                "左半红色标题「你以为的」：画一条从左下到右上的上升曲线，纵轴写「Agent智能」，横轴写「时间」。"
                "右半绿色标题「实际上的」：画一条完全水平的实线标注「Agent智能：不变」，"
                "上面叠加一条上升的蓝色虚线标注「你的prompt技巧在增长」。"
                "底部居中大号加粗中文「不是它在进步，是你在进步」。"
                "风格：简洁的数据对比图表。"
            ),
            "size": "1024x1536",
        },
        "d2_fig2": {
            "prompt": (
                "前后对比信息图，竖版3:4，浅蓝色背景。"
                "上半部分红色标签「改变前」："
                "三个简笔画程序员图标（同事A、同事B、同事C），每人头顶一个独立的思维气泡，气泡里写着不同的技术知识。"
                "人物之间没有任何连线，彼此孤立。"
                "旁边注释文字「同事A踩过的坑，同事B正在重新踩」。"
                "下半部分绿色标签「改变后」："
                "三个程序员图标全部通过线条连接到中央一个六边形节点，节点里写着「xskill」。"
                "知识气泡汇聚到中央节点。注释文字「一人踩坑，全队受益」。"
                "风格：干净的组织结构图风格。"
            ),
            "size": "1024x1536",
        },
        "d2_fig3": {
            "prompt": (
                "流程时间线图，正方形1:1，白色背景。"
                "5个彩色圆角节点从左到右排列，用箭头连接："
                "1.蓝色「新skill版本」→ 2.黄色「提交staging」→ 3.橙色「20%用户拿新版」→ 4.绿色「收集体验分」→ 5.紫色「均分对比」"
                "第5个节点分出两条路：上方绿色「上线 ✓」，下方红色「丢弃 ✗」。"
                "第4个节点旁有红色醒目标注「关键：不是LLM自评！」。"
                "风格：专业的产品流程图。"
            ),
            "size": "1024x1024",
        },
        "d2_fig4": {
            "prompt": (
                "概念示意图，正方形1:1，浅色背景。"
                "画面中央是一个大的透明量杯，里面有蓝色渐变水位。"
                "量杯外有4个水滴正在落入杯中，每个水滴旁标注分数「3分」「2分」「5分」「1分」。"
                "量杯上有一条红色虚线横穿，标注「阈值=10」。"
                "水位接近但还未到达红线。"
                "下方大号中文标题「够了才动手，不急」。"
                "副标题小字「低质量证据自然稀释」。"
                "风格：扁平设计概念图，配色柔和。"
            ),
            "size": "1024x1024",
        },

        # === 方案三：人味叙事型 ===
        "d3_cover": {
            "prompt": (
                "竖版3:4社交媒体封面图，模拟微信群聊截图风格，白色背景。"
                "顶部是微信风格的绿色标题栏，白色文字显示群名「技术群 (6)」。"
                "中间展示4条微信聊天气泡："
                "灰色气泡（对方发）：「你之前配nginx反代怎么搞定的？Claude Code又在瞎建议」"
                "绿色气泡（自己发）：「等下我发你我的CLAUDE.md」"
                "灰色气泡（对方发）：「算了 问了三个人 每个人写法都不一样」"
                "底部深灰色区域，居中白色文字「这件事重复了第47次之后我终于受不了了」。"
                "风格：真实的微信截图感，不要卡通化。"
            ),
            "size": "1024x1536",
        },
        "d3_fig1": {
            "prompt": (
                "模拟微信群聊界面截图，竖版3:4，白色背景。"
                "标题栏显示「项目技术群 (6)」。"
                "聊天内容依次是："
                "小张（灰色气泡）：「Claude Code老是把proxy_pass写成带尾斜杠的，怎么治？」"
                "我（绿色气泡）：「等下我发你我的写法」"
                "小李（灰色气泡）：「我也遇到过 我的解决方法不一样」"
                "小王（灰色气泡）：「我直接在CLAUDE.md里贴了一整段模板」"
                "中间灰色时间标记「—— 10分钟 ——」"
                "小张（灰色气泡）：「要不我们建个共享文档？」"
                "我（绿色气泡）：「上次建的那个谁更新了？」"
                "风格：逼真的微信群聊截图。"
            ),
            "size": "1024x1536",
        },
        "d3_fig2": {
            "prompt": (
                "手写风格计算图，正方形1:1，浅黄色便签纸背景。"
                "标题用手写风格大字「算一笔账」。"
                "下方是大号手写风格算式："
                "单次探索成本：1~3小时"
                "× 团队人数：6人"
                "× 半年重复次数：约15次"
                "─────────"
                "= 540小时 ≈ 3个人月"
                "底部有红色手写下划线，文字「不是token的浪费，是人的时间」。"
                "风格：像在白板上用马克笔算账的感觉，有手写质感。"
            ),
            "size": "1024x1024",
        },
        "d3_fig3": {
            "prompt": (
                "前后对比信息图，竖版3:4，白色背景。"
                "上半部分红色调标签「改变前」："
                "6个简笔画人物图标分两行排列，每人旁边一个文件图标写着「CLAUDE.md」。"
                "人物之间没有连线，各自为战。每人头顶有不同的小气泡写着零碎知识。"
                "标注中文「各自为战，经验隔离」。"
                "中间黑色分隔线，上面标注「pip install xskill」。"
                "下半部分绿色调标签「改变后」："
                "6个人物通过线条连接到中央服务器图标，标注「xskill server」。"
                "服务器上方有文件图标写着「SKILL.md」。"
                "四周环绕4个标签「自动收集」「脱敏上传」「灰度发布」「行为打分」。"
                "标注中文「一人踩坑，全队受益」。"
                "风格：产品经理画的简洁示意图。"
            ),
            "size": "1024x1536",
        },
        "d3_fig4": {
            "prompt": (
                "模拟macOS终端窗口截图，正方形1:1。"
                "终端有圆角边框，顶部红黄绿三个小圆点，深色标题栏。"
                "黑色背景，彩色等宽字体显示中文日志输出："
                "[xskill] 发现3条新轨迹（青色）"
                "[xskill] 拆分为12个原子任务（白色）"
                "[xskill] 路由到4个已有skill（黄色）"
                "[xskill] skill「nginx-proxy」积分：7→13（阈值=10）（橙色）"
                "[xskill] 触发SkillEditAgent编辑「nginx-proxy」（粉色）"
                "[xskill] 提交到staging分支（紫色）"
                "[xskill] 灰度测试启动（20%用户）（黄色）"
                "[xskill] staging体验分7.8 > main体验分6.2 → 已上线 ✓（绿色加粗）"
                "风格：真实的终端日志，每行不同颜色高亮。"
            ),
            "size": "1024x1024",
        },
    }

    if task_name == "all":
        targets = TASKS
    else:
        targets = {task_name: TASKS[task_name]}

    for name, cfg in targets.items():
        out = os.path.join(OUT_DIR, f"{name}.png")
        if os.path.exists(out) and task_name == "all":
            print(f"[SKIP] {name}.png exists", flush=True)
            continue
        print(f"[START] {name}", flush=True)
        try:
            generate_image(cfg["prompt"], f"{name}.png", cfg.get("size", "1024x1536"))
        except Exception as e:
            print(f"[ERROR] {name}: {e}", flush=True)
