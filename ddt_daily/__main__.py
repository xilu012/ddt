from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from .desktop import WindowsDesktop, countdown, select_box
from .engine import AutomationError, Runner, load_workflow, locate, validate_anchor


ROOT = Path(__file__).resolve().parent.parent


def write_config(path: Path, config: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def game_frame(desktop, title):
    countdown()
    desktop.check_stop()
    if title.casefold() not in desktop.title().casefold():
        raise AutomationError(f"前台窗口标题需含 {title!r}，实际为 {desktop.title()!r}。")
    return desktop.screenshot()


def capture_anchor(path, screen, region, image_name, prompt):
    box = select_box(screen, prompt)
    x, y, w, h = box
    rx, ry, rw, rh = region
    if x < rx or y < ry or x + w > rx + rw or y + h > ry + rh:
        raise AutomationError("识别框必须完全位于先前选择的游戏区域内。")
    image = screen.crop((x, y, x + w, y + h))
    validate_anchor(image)
    image.save(path.parent / image_name)
    return {"image": image_name, "position": [x, y]}, image


def record(args, path):
    desktop = WindowsDesktop()
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        print("继续追加步骤；需要从头录制请使用另一个 --profile，避免覆盖原记录。")
    else:
        print("先手动登录 http://ddt.wan.com/，进入弹王宝典。保持浏览器大小和缩放不变。")
        print("仅录制你希望自动执行的操作；录制 click 步骤时会实际点击一次。")
        input("准备好后按回车：")
        screen = game_frame(desktop, args.window_title)
        region = select_box(screen, "框选整个游戏画面，不包括浏览器地址栏")
        config = {"version": 1, "site": "http://ddt.wan.com/", "window_title": args.window_title,
                  "screen_size": list(screen.size), "region": region, "timezone": "Asia/Shanghai",
                  "threshold": 0.94, "search_margin": 40, "steps": []}
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "images").mkdir(exist_ok=True)
        write_config(path, config)

    while True:
        print("\n按弹王宝典从上到下录制；同一任务可拆成多个步骤，最后回宝典确认完成标记。")
        name = input("下一步名称（如 签到/领取奖励；直接回车结束）：").strip()
        if not name:
            break
        action = input("类型：回车=点击；w=只等待某个画面：").strip().lower()
        if action not in ("", "w"):
            print("未知类型，未录制。")
            continue
        # Unique names prevent an interrupted recording from overwriting existing templates.
        import uuid
        prefix = "images/" + uuid.uuid4().hex
        step = {"name": name, "action": "wait" if action == "w" else "click", "timeout": 45}
        before_screen = None
        if step["action"] == "click":
            before_screen = game_frame(desktop, config["window_title"])
            spec, image = capture_anchor(path, before_screen, config["region"], prefix + "-before.png",
                                         "框选要点击的按钮，框的中心就是点击位置")
            step["before"] = spec
            print("接下来会点击一次刚选的按钮。")
            current = game_frame(desktop, config["window_title"])
            if list(current.size) != config["screen_size"]:
                raise AutomationError("屏幕尺寸改变，请恢复后重试。")
            position = locate(current, image, config["region"], spec["position"],
                              config["search_margin"], config["threshold"])
            if position is None:
                raise AutomationError("点击前画面已变化，没有发送点击。")
            desktop.check_stop()
            desktop.click(*position)
        input("等待游戏响应；准备选择结束画面的识别框时，回终端按回车：")
        after_screen = game_frame(desktop, config["window_title"])
        if list(after_screen.size) != config["screen_size"]:
            raise AutomationError("屏幕尺寸改变，请恢复后重试。")
        spec, image = capture_anchor(path, after_screen, config["region"], prefix + "-after.png",
                                     "框选操作后才出现的稳定标记（如已领取、弹窗标题、任务完成标记）")
        if before_screen is not None and locate(before_screen, image, config["region"],
                                               spec["position"], config["search_margin"],
                                               config["threshold"]) is not None:
            raise AutomationError("该结束标记在点击前已存在，无法证明点击成功。本步未保存，请重新录制。")
        step["after"] = spec
        config["steps"].append(step)
        write_config(path, config)
        print(f"已保存第 {len(config['steps'])} 步：{name}")
    print(f"录制文件：{path}\n先使用 check 检查配置，再从相同游戏起始画面运行 run。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="弹弹堂 3：按弹王宝典顺序录制、校验和重放本机日常流程")
    parser.add_argument("--profile", default="default", help="角色配置名；多角色分别录制")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="交互录制，操作会实际执行")
    rec.add_argument("--window-title", default="弹弹堂", help="浏览器前台窗口标题必须包含的文字")
    sub.add_parser("check", help="离线检查配置和所有识别模板，不操作桌面")
    run = sub.add_parser("run", help="登录后，从录制的起始画面执行一次日常")
    run.add_argument("--force", action="store_true", help="检查游戏状态后明确重跑，不自动跳过旧步骤")
    run.add_argument("--max-minutes", type=float, default=15, help="本轮最长运行分钟数")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", args.profile):
        parser.error("profile 仅允许 1–48 个英文字母、数字、下划线或短横线")
    path = ROOT / "profiles" / args.profile / "workflow.json"
    try:
        if args.command == "record":
            record(args, path)
        elif args.command == "check":
            config = load_workflow(path)
            print(f"配置与模板检查通过，共 {len(config['steps'])} 步（未验证真实游戏）。")
            for i, step in enumerate(config["steps"], 1):
                print(f"  {i}. {step['name']} ({step['action']})")
        else:
            desktop = WindowsDesktop()
            runner = Runner(path, desktop)
            countdown()
            try:
                runner.run(force=args.force, max_seconds=args.max_minutes * 60)
            except Exception:
                # Keep only a local game-region screenshot; never save login credentials.
                folder = ROOT / "runs" / args.profile
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    if runner.config["window_title"].casefold() in desktop.title().casefold():
                        x, y, w, h = runner.config["region"]
                        desktop.screenshot().crop((x, y, x + w, y + h)).save(folder / f"failed-{time.time_ns()}.png")
                        print(f"失败时的游戏截图保存在 {folder}", file=sys.stderr)
                except Exception:
                    pass
                raise
    except KeyboardInterrupt:
        print("已停止。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"停止：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
