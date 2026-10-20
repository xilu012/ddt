from __future__ import annotations

import hashlib
import json
import time
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from PIL import Image


class AutomationError(RuntimeError):
    pass


@contextmanager
def profile_lock(directory: Path):
    """OS locks are released even when a process is killed; no stale PID files."""
    with (directory / ".run.lock").open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AutomationError("此角色已有执行进程，请先停止原进程。") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AutomationError("此角色已有执行进程，请先停止原进程。") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Desktop(Protocol):
    def screenshot(self) -> Image.Image: ...
    def click(self, x: int, y: int) -> None: ...
    def title(self) -> str: ...
    def check_stop(self) -> None: ...


@dataclass
class Clock:
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"))


def validate_anchor(image: Image.Image) -> None:
    if min(image.size) < 12:
        raise AutomationError("识别框至少需要 12×12 像素。")
    if gray(image).std() < 8:
        raise AutomationError("识别框过于单一，请框选含图标和文字的区域。")


def locate(screen: Image.Image, anchor: Image.Image, region: list[int],
           original: list[int], margin: int, threshold: float) -> tuple[int, int] | None:
    """Match only near the recorded location, inside the selected game region."""
    rx, ry, rw, rh = region
    ax, ay = original
    aw, ah = anchor.size
    left, top = max(rx, ax - margin), max(ry, ay - margin)
    right = min(rx + rw, ax + aw + margin, screen.width)
    bottom = min(ry + rh, ay + ah + margin, screen.height)
    if right - left < aw or bottom - top < ah:
        return None
    result = cv2.matchTemplate(gray(screen.crop((left, top, right, bottom))),
                               gray(anchor), cv2.TM_CCOEFF_NORMED)
    _, score, _, position = cv2.minMaxLoc(result)
    if not np.isfinite(score) or score < threshold:
        return None
    # Two matching buttons are unsafe: ask for a more distinctive recording.
    px, py = position
    result[max(0, py - ah // 2):py + ah // 2 + 1,
           max(0, px - aw // 2):px + aw // 2 + 1] = -1
    if result.size and float(result.max()) >= threshold:
        raise AutomationError("识别到多个相似目标，请缩小搜索范围或重新录制识别框。")
    return left + px + aw // 2, top + py + ah // 2


def load_workflow(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("version") != 1 or not config.get("steps"):
        raise AutomationError("没有可执行的步骤，请先 record。")
    for key in ("screen_size", "region", "window_title"):
        if not config.get(key):
            raise AutomationError(f"缺少配置：{key}")
    sw, sh = config["screen_size"]
    x, y, w, h = config["region"]
    if min(w, h) < 12 or x < 0 or y < 0 or x + w > sw or y + h > sh:
        raise AutomationError("游戏区域不在录制的屏幕范围内。")
    ZoneInfo(config.get("timezone", "Asia/Shanghai"))
    if not 0.5 <= config.get("threshold", 0.94) <= 1:
        raise AutomationError("threshold 必须在 0.5 到 1 之间。")
    if not 0 <= config.get("search_margin", 40) <= 200:
        raise AutomationError("search_margin 必须在 0 到 200 之间。")
    for step in config["steps"]:
        if step.get("action") not in ("click", "wait"):
            raise AutomationError("只支持 click 和 wait 步骤。")
        if not 0 < step.get("timeout", 30) <= 600:
            raise AutomationError("步骤 timeout 必须在 0 到 600 秒之间。")
        for key in (("before", "after") if step["action"] == "click" else ("after",)):
            spec = step[key]
            template_path = (path.parent / spec["image"]).resolve()
            if not template_path.is_relative_to(path.parent.resolve()):
                raise AutomationError("模板路径必须在当前配置目录内。")
            with Image.open(template_path) as image:
                validate_anchor(image)
                ax, ay = spec["position"]
                if ax < x or ay < y or ax + image.width > x + w or ay + image.height > y + h:
                    raise AutomationError("识别框不在游戏区域内。")
    return config


class Runner:
    def __init__(self, path: Path, desktop: Desktop, clock: Clock | None = None,
                 log: Callable[[str], None] = print):
        self.path, self.desktop = path, desktop
        self.config = load_workflow(path)
        self.clock, self.log = clock or Clock(), log
        self.deadline = float("inf")

    def frame(self) -> Image.Image:
        self.desktop.check_stop()
        if self.clock.now() >= self.deadline:
            raise AutomationError("超过本轮总时限，停止执行。")
        if self.config["window_title"].casefold() not in self.desktop.title().casefold():
            raise AutomationError("当前前台窗口不是游戏窗口，停止执行。")
        screen = self.desktop.screenshot()
        if list(screen.size) != self.config["screen_size"]:
            raise AutomationError("屏幕尺寸发生变化，请恢复录制时的分辨率或重新录制。")
        return screen

    def wait(self, spec: dict, timeout: float) -> tuple[int, int]:
        with Image.open(self.path.parent / spec["image"]) as image:
            anchor = image.copy()
        end = self.clock.now() + timeout
        while True:
            position = locate(self.frame(), anchor, self.config["region"], spec["position"],
                              self.config.get("search_margin", 40),
                              self.config.get("threshold", 0.94))
            if position is not None:
                return position
            if self.clock.now() >= end:
                raise AutomationError(f"等待画面超时：{spec['image']}")
            self.clock.sleep(0.3)

    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.path.read_bytes())
        for step in self.config["steps"]:
            for key in ("before", "after"):
                if key in step:
                    digest.update((self.path.parent / step[key]["image"]).read_bytes())
        return digest.hexdigest()

    def run(self, *, force: bool = False, max_seconds: float = 900) -> bool:
        with profile_lock(self.path.parent):
            return self._run(force=force, max_seconds=max_seconds)

    def _run(self, *, force: bool, max_seconds: float) -> bool:
        if max_seconds <= 0:
            raise AutomationError("总时限必须大于零。")
        state_path = self.path.parent / "state.json"
        day = datetime.now(ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))).date().isoformat()
        fingerprint = self.fingerprint()
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if not force and state.get("day") == day:
            if state.get("status") == "completed" and state.get("fingerprint") == fingerprint:
                self.log("今天已完成此流程，跳过。")
                return False
            raise AutomationError("今天已有未完成记录或流程已改变。请检查游戏状态，再用 --force 明确重跑。")
        def save(status: str) -> None:
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"day": day, "status": status,
                                             "fingerprint": fingerprint}, indent=2), encoding="utf-8")
            temporary.replace(state_path)

        self.deadline = self.clock.now() + max_seconds
        self.frame()  # Validate the desktop before creating an in-progress record.
        save("running")
        for index, step in enumerate(self.config["steps"], 1):
            self.log(f"[{index}/{len(self.config['steps'])}] {step['name']}")
            timeout = step.get("timeout", 30)
            if step["action"] == "click":
                position = self.wait(step["before"], timeout)
                self.frame()  # Recheck focus immediately before sending input.
                self.desktop.click(*position)
            self.wait(step["after"], timeout)
        save("completed")
        self.log("全部步骤的结束画面已确认，已记录今日完成。")
        return True
