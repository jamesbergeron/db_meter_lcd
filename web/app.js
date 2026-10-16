const BAR_SEGMENTS = 35;
const DB_MIN = 30;
const DB_MAX = 110;

let state = {
  db: 0,
  status: "CONNECTING",
  connected: false,
  min_db: null,
  max_db: null,
  avg_db: null,
  sample_count: 0,
  alarm_threshold: 105,
  alarm_active: false,
};

let peakHold = 30;
let peakTimer = 0;
let flashOn = false;
let eventSource = null;

const el = {
  db: document.getElementById("db-display"),
  dbPanel: document.getElementById("display-panel"),
  alarmBanner: document.getElementById("alarm-banner"),
  badgeConn: document.getElementById("badge-conn"),
  badgeAlarm: document.getElementById("badge-alarm"),
  statMin: document.getElementById("stat-min"),
  statMax: document.getElementById("stat-max"),
  statAvg: document.getElementById("stat-avg"),
  statCur: document.getElementById("stat-cur"),
  barTrack: document.getElementById("bar-track"),
  settingsDialog: document.getElementById("settings-dialog"),
  statusMsg: document.getElementById("settings-status"),
};

function formatDb(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Number(v).toFixed(1)} dB`;
}

function buildBar() {
  el.barTrack.innerHTML = "";
  for (let i = 0; i < BAR_SEGMENTS; i++) {
    const seg = document.createElement("div");
    seg.className = "bar-seg";
    seg.dataset.index = String(i);
    el.barTrack.appendChild(seg);
  }
}

function updateBar(db) {
  const clamped = Math.max(DB_MIN, Math.min(db, DB_MAX));
  const ratio = (clamped - DB_MIN) / (DB_MAX - DB_MIN);
  const filled = Math.floor(ratio * BAR_SEGMENTS);

  if (db > peakHold) {
    peakHold = db;
    peakTimer = 15;
  } else if (peakTimer > 0) {
    peakTimer -= 1;
  } else {
    peakHold = Math.max(db, peakHold - 0.8);
  }
  const peakRatio = (Math.max(DB_MIN, Math.min(peakHold, DB_MAX)) - DB_MIN) / (DB_MAX - DB_MIN);
  const peakSeg = Math.floor(peakRatio * (BAR_SEGMENTS - 1));

  const segs = el.barTrack.querySelectorAll(".bar-seg");
  segs.forEach((seg, i) => {
    seg.classList.remove("fill-low", "fill-mid", "fill-high");
    if (i < filled) {
      const r = i / BAR_SEGMENTS;
      if (r < 0.65) seg.classList.add("fill-low");
      else if (r < 0.88) seg.classList.add("fill-mid");
      else seg.classList.add("fill-high");
    }
    seg.style.outline = i === peakSeg && peakRatio > 0.05 ? "2px solid #ffd700" : "";
  });
}

function applyTelemetry(data) {
  if (data.alarm_threshold != null) {
    state.alarm_threshold = data.alarm_threshold;
    el.badgeAlarm.textContent = `ALARM: >${state.alarm_threshold} dB`;
  }

  state.connected = !!data.connected;
  state.status = data.status || "UNKNOWN";

  if (data.status === "OK" && typeof data.db === "number") {
    state.db = data.db;
    if (data.min_db != null) state.min_db = data.min_db;
    if (data.max_db != null) state.max_db = data.max_db;
    if (data.avg_db != null) state.avg_db = data.avg_db;
  }

  state.alarm_active = !!data.alarm_active;

  el.statCur.textContent = formatDb(state.db);
  el.statMin.textContent = formatDb(state.min_db);
  el.statMax.textContent = formatDb(state.max_db);
  el.statAvg.textContent = formatDb(state.avg_db);

  const dbStr = state.status === "OK" ? state.db.toFixed(1) : "—";
  el.db.textContent = dbStr;
  el.db.classList.toggle("alarm", state.alarm_active);

  if (state.connected && state.status === "OK") {
    el.badgeConn.textContent = `● ONLINE (${data.source_label || "NET"})`;
    el.badgeConn.className = "badge online";
  } else if (state.connected) {
    el.badgeConn.textContent = `● SENSOR (${state.status})`;
    el.badgeConn.className = "badge offline";
  } else {
    el.badgeConn.textContent = "● OFFLINE";
    el.badgeConn.className = "badge offline";
  }

  el.badgeAlarm.classList.toggle("alarm-on", state.alarm_active);
  el.alarmBanner.classList.toggle("visible", state.alarm_active);
  el.alarmBanner.textContent = `🚨 HIGH SOUND PRESSURE — ${state.db.toFixed(1)} dB (limit ${state.alarm_threshold} dB) 🚨`;

  updateBar(state.db);
}

function connectStream() {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource("/api/stream");
  eventSource.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      applyTelemetry(data);
    } catch (e) {
      console.warn("Bad SSE payload", e);
    }
  };
  eventSource.onerror = () => {
    el.badgeConn.textContent = "● RECONNECTING…";
    el.badgeConn.className = "badge offline";
  };
}

async function loadConfigIntoForm() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  document.getElementById("cfg-tcp-host").value = cfg.tcp_host || "";
  document.getElementById("cfg-tcp-port").value = cfg.tcp_port ?? 55124;
  document.getElementById("cfg-threshold").value = cfg.alarm_threshold ?? 105;
  document.getElementById("cfg-cooldown").value = cfg.alarm_cooldown_seconds ?? 60;
  document.getElementById("cfg-discord-enabled").checked = !!cfg.discord_enabled;
  document.getElementById("cfg-discord-url").value = cfg.discord_webhook_url || "";
  document.getElementById("cfg-discord-user").value = cfg.discord_user_id || "";
  document.getElementById("cfg-ntfy-enabled").checked = !!cfg.ntfy_enabled;
  document.getElementById("cfg-ntfy-server").value = cfg.ntfy_server || "https://ntfy.sh";
  document.getElementById("cfg-ntfy-topic").value = cfg.ntfy_topic || "";
  document.getElementById("cfg-ntfy-priority").value = cfg.ntfy_priority || "high";
}

function readConfigFromForm() {
  return {
    tcp_host: document.getElementById("cfg-tcp-host").value.trim(),
    tcp_port: parseInt(document.getElementById("cfg-tcp-port").value, 10),
    alarm_threshold: parseFloat(document.getElementById("cfg-threshold").value),
    alarm_cooldown_seconds: parseInt(document.getElementById("cfg-cooldown").value, 10),
    discord_enabled: document.getElementById("cfg-discord-enabled").checked,
    discord_webhook_url: document.getElementById("cfg-discord-url").value.trim(),
    discord_user_id: document.getElementById("cfg-discord-user").value.trim(),
    ntfy_enabled: document.getElementById("cfg-ntfy-enabled").checked,
    ntfy_server: document.getElementById("cfg-ntfy-server").value.trim().replace(/\/$/, ""),
    ntfy_topic: document.getElementById("cfg-ntfy-topic").value.trim(),
    ntfy_priority: document.getElementById("cfg-ntfy-priority").value.trim() || "high",
  };
}

function setStatusMsg(text, ok) {
  el.statusMsg.textContent = text;
  el.statusMsg.className = "status-msg " + (ok ? "ok" : "err");
}

async function saveSettings() {
  const body = readConfigFromForm();
  if (!body.tcp_host) {
    setStatusMsg("TCP host is required (use 127.0.0.1 on the Pi).", false);
    return;
  }
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    setStatusMsg(data.error || "Save failed", false);
    return;
  }
  setStatusMsg("Settings saved to web_config.json on the server.", true);
}

async function testDiscord() {
  const body = readConfigFromForm();
  const res = await fetch("/api/test/discord", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      discord_webhook_url: body.discord_webhook_url,
      discord_user_id: body.discord_user_id,
      alarm_threshold: body.alarm_threshold,
    }),
  });
  const data = await res.json();
  setStatusMsg(data.message || data.error, data.ok);
}

async function testNtfy() {
  const body = readConfigFromForm();
  const res = await fetch("/api/test/ntfy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ntfy_server: body.ntfy_server,
      ntfy_topic: body.ntfy_topic,
      ntfy_priority: body.ntfy_priority,
      alarm_threshold: body.alarm_threshold,
    }),
  });
  const data = await res.json();
  setStatusMsg(data.message || data.error, data.ok);
}

function resetStats() {
  fetch("/api/stats/reset", { method: "POST" }).catch(() => {});
}

document.getElementById("btn-settings").addEventListener("click", async () => {
  await loadConfigIntoForm();
  setStatusMsg("", true);
  el.settingsDialog.showModal();
});

document.getElementById("btn-save").addEventListener("click", saveSettings);
document.getElementById("btn-test-discord").addEventListener("click", testDiscord);
document.getElementById("btn-test-ntfy").addEventListener("click", testNtfy);
document.getElementById("btn-reset-stats").addEventListener("click", resetStats);
document.getElementById("btn-close-settings").addEventListener("click", () => el.settingsDialog.close());

setInterval(() => {
  if (!state.alarm_active) {
    document.body.classList.remove("alarm-flash");
    return;
  }
  flashOn = !flashOn;
  document.body.classList.toggle("alarm-flash", flashOn);
}, 200);

buildBar();
connectStream();
