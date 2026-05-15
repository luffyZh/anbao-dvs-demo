const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const crypto = require("crypto");
const express = require("express");

const ROOT = __dirname;
const EVENTS_DIR = path.join(ROOT, "events");
const STATIC_DIR = path.join(ROOT, "static");
const ASSETS_DIR = path.join(STATIC_DIR, "assets");

function nowCnIso() {
  // 固定 +08:00，便于演示
  const tzOffsetMin = 8 * 60;
  const now = new Date(Date.now() + tzOffsetMin * 60 * 1000);
  // 用 ISO 形式输出，并拼接固定时区
  // 示例：2026-04-23T15:30:12.120+08:00
  const iso = now.toISOString().replace("Z", "");
  return `${iso}+08:00`;
}

async function listEventFiles() {
  // 递归遍历 events 目录，返回 *.json（按 mtime 倒序）
  async function walk(dir) {
    const out = [];
    const items = await fsp.readdir(dir, { withFileTypes: true }).catch(() => []);
    for (const it of items) {
      const p = path.join(dir, it.name);
      if (it.isDirectory()) out.push(...(await walk(p)));
      else if (it.isFile() && it.name.toLowerCase().endsWith(".json")) out.push(p);
    }
    return out;
  }
  const files = await walk(EVENTS_DIR);
  const withStat = await Promise.all(
    files.map(async (p) => ({ p, st: await fsp.stat(p).catch(() => null) }))
  );
  return withStat
    .filter((x) => x.st)
    .sort((a, b) => b.st.mtimeMs - a.st.mtimeMs)
    .map((x) => x.p);
}

async function safeReadJson(p) {
  try {
    const t = await fsp.readFile(p, "utf-8");
    return JSON.parse(t);
  } catch {
    return null;
  }
}

function normalizeEvent(e) {
  const artifact = { ...(e.artifact || {}) };
  if (artifact.image) artifact.image_url = `/assets/${artifact.image}`;
  if (artifact.video) artifact.video_url = `/assets/${artifact.video}`;
  return { ...e, artifact };
}

async function ensureSeedEvents() {
  await fsp.mkdir(EVENTS_DIR, { recursive: true });
  const existing = await listEventFiles();
  if (existing.length) return;

  const dayDir = path.join(EVENTS_DIR, new Date().toISOString().slice(0, 10));
  await fsp.mkdir(dayDir, { recursive: true });

  async function writeOne(idx, alarmType, matched, countEst) {
    const eventId = `gate1_e${String(idx).padStart(4, "0")}`;
    const ts = nowCnIso();
    const videoName = idx % 2 === 1 ? "placeholder.mp4" : "placeholder2.mp4";
    const payload = {
      event_id: eventId,
      device_id: "dm3_demo_01",
      site: "仓库门口-1",
      ts_start: ts,
      ts_end: ts,
      has_person: true,
      person_count_est: countEst,
      direction: "in",
      confidence: alarmType ? 0.86 : 0.78,
      ledger_match: {
        matched,
        reason: matched
          ? "demo"
          : alarmType === "多人同行"
          ? "ledger_count=1, detected=2"
          : "未找到匹配台账",
      },
      alarm: {
        level: alarmType ? "high" : "none",
        type: alarmType || "无",
      },
      artifact: {
        video: videoName
      },
      status: alarmType ? "未处理" : "合规",
    };
    await fsp.writeFile(
      path.join(dayDir, `${eventId}.json`),
      JSON.stringify(payload, null, 2),
      "utf-8"
    );
  }

  await writeOne(1, "多人同行", false, 2);
  await writeOne(2, "", true, 1);
}

async function findEventFileById(eventId) {
  const files = await listEventFiles();
  for (const p of files) {
    const e = await safeReadJson(p);
    if (e && e.event_id === eventId) return { p, e };
  }
  return null;
}

async function main() {
  await ensureSeedEvents();

  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "256kb" }));

  // 静态托管（大屏与素材）
  app.use("/", express.static(STATIC_DIR, { etag: false, lastModified: false, maxAge: 0 }));
  app.use("/assets", express.static(ASSETS_DIR, { etag: false, lastModified: false, maxAge: 0 }));

  app.get("/health", (req, res) => res.json({ ok: true }));

  app.get("/api/events", async (req, res) => {
    await ensureSeedEvents();
    const files = await listEventFiles();
    const events = [];
    for (const p of files.slice(0, 200)) {
      const e = await safeReadJson(p);
      if (e) events.push(normalizeEvent(e));
    }
    res.json({ ok: true, events });
  });

  // 一键模拟新事件（演示用）
  app.post("/api/simulate", async (req, res) => {
    await ensureSeedEvents();
    const alarm_type = req.body?.alarm_type || "无台账进入";
    const person_count_est = Number(req.body?.person_count_est || 2);

    const dayDir = path.join(EVENTS_DIR, new Date().toISOString().slice(0, 10));
    await fsp.mkdir(dayDir, { recursive: true });

    const event_id = `gate1_${crypto.randomBytes(4).toString("hex")}`;
    const ts = nowCnIso();
    // 让“模拟新事件”在两个视频之间轮换，便于演示“不同事件有不同复核片段”
    const existingFiles = await listEventFiles();
    const video =
      existingFiles.length % 2 === 0 ? "placeholder.mp4" : "placeholder2.mp4";
    const payload = {
      event_id,
      device_id: "dm3_demo_01",
      site: "仓库门口-1",
      ts_start: ts,
      ts_end: ts,
      has_person: true,
      person_count_est,
      direction: "in",
      confidence: 0.82,
      ledger_match: { matched: false, reason: "demo-未匹配" },
      alarm: { level: "high", type: alarm_type },
      artifact: { video },
      status: "未处理",
    };

    await fsp.writeFile(
      path.join(dayDir, `${event_id}.json`),
      JSON.stringify(payload, null, 2),
      "utf-8"
    );
    res.json({ ok: true, event: normalizeEvent(payload) });
  });

  // 处置状态写回（演示用）
  app.post("/api/events/:eventId/status", async (req, res) => {
    const { eventId } = req.params;
    const status = req.body?.status;
    const allow = new Set(["未处理", "已确认", "误报", "合规"]);
    if (!allow.has(status)) return res.status(400).json({ ok: false, error: "非法状态" });

    const found = await findEventFileById(eventId);
    if (!found) return res.status(404).json({ ok: false, error: "事件不存在" });

    const next = { ...found.e, status };
    await fsp.writeFile(found.p, JSON.stringify(next, null, 2), "utf-8");
    res.json({ ok: true });
  });

  const port = Number(process.env.PORT || 8000);
  app.listen(port, "0.0.0.0", () => {
    console.log(`H安保 Demo server: http://localhost:${port}/`);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
