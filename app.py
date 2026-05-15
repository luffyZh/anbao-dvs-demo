"""
单页大屏交互 Demo（零依赖版）

为什么不用 Flask：
- 沙箱环境里可能存在系统预装包不可卸载问题，pip 安装会失败
- Demo 目标是“交互演示”，用标准库 http.server 即可
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
EVENTS_DIR = ROOT / "events"
STATIC_DIR = ROOT / "static"
ASSETS_DIR = STATIC_DIR / "assets"


def now_cn_iso():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="milliseconds")


def safe_read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_event_files():
    if not EVENTS_DIR.exists():
        return []
    return sorted(EVENTS_DIR.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def normalize_event(e: dict):
    artifact = (e.get("artifact") or {}).copy()
    if artifact.get("image"):
        artifact["image_url"] = f"/assets/{artifact['image']}"
    if artifact.get("video"):
        artifact["video_url"] = f"/assets/{artifact['video']}"
    e["artifact"] = artifact
    return e


def ensure_seed_events():
    if any(list_event_files()):
        return

    day_dir = EVENTS_DIR / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    def write_one(idx: int, alarm_type: str, matched: bool, count_est: int):
        event_id = f"gate1_e{idx:04d}"
        ts = now_cn_iso()
        payload = {
            "event_id": event_id,
            "device_id": "dm3_demo_01",
            "site": "仓库门口-1",
            "ts_start": ts,
            "ts_end": ts,
            "has_person": True,
            "person_count_est": count_est,
            "direction": "in",
            "confidence": 0.86 if alarm_type else 0.78,
            "ledger_match": {
                "matched": matched,
                "reason": "demo"
                if matched
                else ("ledger_count=1, detected=2" if alarm_type == "多人同行" else "未找到匹配台账"),
            },
            "alarm": {"level": "high" if alarm_type else "none", "type": alarm_type or "无"},
            "artifact": {"image": "placeholder.gif", "video": "placeholder.mp4"},
            "status": "未处理" if alarm_type else "合规",
        }
        (day_dir / f"{event_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    write_one(1, "多人同行", False, 2)
    write_one(2, "", True, 1)


def json_bytes(obj, status=HTTPStatus.OK):
    return status, json.dumps(obj, ensure_ascii=False).encode("utf-8")


def guess_type(filename: str):
    fn = filename.lower()
    if fn.endswith(".html"):
        return "text/html; charset=utf-8"
    if fn.endswith(".js"):
        return "text/javascript; charset=utf-8"
    if fn.endswith(".css"):
        return "text/css; charset=utf-8"
    if fn.endswith(".gif"):
        return "image/gif"
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return "image/jpeg"
    if fn.endswith(".mp4"):
        return "video/mp4"
    if fn.endswith(".json"):
        return "application/json; charset=utf-8"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 演示环境减少日志噪音
        return

    def _send(self, status: int, body: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guess_type(path.name))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        ensure_seed_events()
        url = urlparse(self.path)
        path = url.path

        if path == "/" or path == "/index.html":
            return self._send_file(STATIC_DIR / "index.html")

        if path == "/health":
            status, body = json_bytes({"ok": True})
            return self._send(status, body)

        if path == "/api/events":
            events = []
            for p in list_event_files()[:200]:
                e = safe_read_json(p)
                if e:
                    events.append(normalize_event(e))
            status, body = json_bytes({"ok": True, "events": events})
            return self._send(status, body)

        m = re.match(r"^/assets/(.+)$", path)
        if m:
            filename = m.group(1)
            # 防止目录穿越
            safe = os.path.basename(filename)
            return self._send_file(ASSETS_DIR / safe)

        return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        ensure_seed_events()
        url = urlparse(self.path)
        path = url.path

        if path == "/api/simulate":
            body = self._read_json_body()
            alarm_type = body.get("alarm_type") or "无台账进入"
            count_est = int(body.get("person_count_est") or 2)

            day_dir = EVENTS_DIR / datetime.now().strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            event_id = f"gate1_{uuid.uuid4().hex[:8]}"
            ts = now_cn_iso()

            payload = {
                "event_id": event_id,
                "device_id": "dm3_demo_01",
                "site": "仓库门口-1",
                "ts_start": ts,
                "ts_end": ts,
                "has_person": True,
                "person_count_est": count_est,
                "direction": "in",
                "confidence": 0.82,
                "ledger_match": {"matched": False, "reason": "demo-未匹配"},
                "alarm": {"level": "high", "type": alarm_type},
                "artifact": {"image": "placeholder.gif", "video": "placeholder.mp4"},
                "status": "未处理",
            }
            (day_dir / f"{event_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            status, out = json_bytes({"ok": True, "event": normalize_event(payload)})
            return self._send(status, out)

        m = re.match(r"^/api/events/([^/]+)/status$", path)
        if m:
            event_id = m.group(1)
            body = self._read_json_body()
            status_str = body.get("status")
            if status_str not in ("未处理", "已确认", "误报", "合规"):
                status, out = json_bytes({"ok": False, "error": "非法状态"}, HTTPStatus.BAD_REQUEST)
                return self._send(status, out)

            target = None
            target_json = None
            for p in list_event_files():
                e = safe_read_json(p)
                if e and e.get("event_id") == event_id:
                    target = p
                    target_json = e
                    break
            if not target:
                status, out = json_bytes({"ok": False, "error": "事件不存在"}, HTTPStatus.NOT_FOUND)
                return self._send(status, out)

            target_json["status"] = status_str
            target.write_text(json.dumps(target_json, ensure_ascii=False, indent=2), encoding="utf-8")
            status, out = json_bytes({"ok": True})
            return self._send(status, out)

        status, out = json_bytes({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
        return self._send(status, out)


def main():
    port = int(os.environ.get("PORT", "8000"))
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_seed_events()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on http://localhost:{port}/")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
