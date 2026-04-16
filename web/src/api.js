// Shared API client for traj2skill web UI.
// Same-origin: FastAPI serves the SPA and /api/v1/* together.

export const BASE = "";

async function jget(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`GET ${path} -> ${r.status}: ${text}`);
  }
  return r.json();
}

async function jpost(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`POST ${path} -> ${r.status}: ${text}`);
  }
  return r.json();
}

// ---- Chat ----
export function chatMessage(message, traj_id) {
  return jpost("/api/v1/chat", { message, traj_id });
}

export function chatMessageStream(message, { traj_id, session_id } = {}, { onEvent, signal } = {}) {
  return sseStream("/api/v1/chat/stream", { message, traj_id, session_id }, { onEvent, signal });
}

export function archiveChat({ messages, skill_name, side, sha, traj_id }) {
  return jpost("/api/v1/chat/archive", { messages, skill_name, side, sha, traj_id });
}

// ---- Canary ----
export function getCanaryOverview() {
  return jget("/api/v1/canary/overview");
}

export function getSkillCanary(name) {
  return jget(`/api/v1/skills/${encodeURIComponent(name)}/canary`);
}

export function getSkillCandidates(name) {
  return jget(`/api/v1/skills/${encodeURIComponent(name)}/candidates`);
}

// ---- Health ----
export function getHealth() {
  return jget("/api/v1/health");
}

export function getStatus() {
  return jget("/api/v1/status");
}

export function searchTrajectories({ query, top_k = 5, filter } = {}) {
  return jpost("/api/v1/trajectories/search", { query, top_k, filter });
}

export function submitTrajectory({ content, format, metadata, traj_id } = {}) {
  return jpost("/api/v1/trajectories/submit", {
    content,
    format,
    metadata,
    traj_id,
  });
}

export function listSkills() {
  return jget("/api/v1/skills");
}

export function getSkill(name) {
  return jget(`/api/v1/skills/${encodeURIComponent(name)}`);
}

export function processTrajectory({ traj_path, dry_run }, { onEvent, signal } = {}) {
  return sseStream(
    "/api/v1/skills/process",
    { traj_path, dry_run: !!dry_run },
    { onEvent, signal }
  );
}

/**
 * Evaluate a skill via the SSE endpoint
 *   POST /api/v1/skills/{name}/eval  { n_runs, sandbox }
 * Emits progress / log / result / error events, matching tasks.py EvalRequest.
 */
export function evalSkill(
  name,
  { n_runs = 3, sandbox = false } = {},
  { onEvent, signal } = {}
) {
  return sseStream(
    `/api/v1/skills/${encodeURIComponent(name)}/eval`,
    { n_runs, sandbox: !!sandbox },
    { onEvent, signal }
  );
}

/**
 * POST `path` with JSON `body`, parse the SSE stream, and invoke
 * `onEvent(type, data)` for each event. Emits a synthetic `done` event
 * when the server closes cleanly, `error` on failure.
 *
 * Returns a Promise that resolves when the stream is fully consumed or
 * rejects if fetch itself fails (network / abort).
 */
// ---- Registry ----
export function listRegistryDirs() {
  return jget("/api/v1/registry/dirs");
}

// ---- Trajectories list ----
export function listTrajectories() {
  return jget("/api/v1/trajectories/list");
}

// ---- Watcher ----
export function getWatcherStatus() {
  return jget("/api/v1/watcher/status");
}

// ---- Trajectory content ----
export function getTrajectoryContent(path) {
  return jget(`/api/v1/trajectories/content?path=${encodeURIComponent(path)}`);
}

// ---- Trajectory process logs ----
export function getTrajectoryLogs(filename, dir) {
  let url = `/api/v1/trajectories/logs?filename=${encodeURIComponent(filename)}`;
  if (dir) url += `&dir=${encodeURIComponent(dir)}`;
  return jget(url);
}

// ---- Skill resolve ----
export function resolveSkill({ query, accept_staging = true }) {
  return jpost("/api/v1/skills/resolve", { query, accept_staging });
}

export async function sseStream(path, body, { onEvent, signal } = {}) {
  const emit = (type, data) => {
    try {
      onEvent && onEvent(type, data);
    } catch (e) {
      // swallow handler errors so stream keeps flowing
      // eslint-disable-next-line no-console
      console.error("sseStream onEvent handler threw:", e);
    }
  };

  let resp;
  try {
    resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    emit("error", { message: String(err?.message || err) });
    throw err;
  }

  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    emit("error", { status: resp.status, message: text || resp.statusText });
    throw new Error(`SSE ${path} -> ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatch = (block) => {
    // An SSE "event" is one block separated by blank lines.
    // Within a block, lines are "event: X" / "data: Y" / "id: ..." / "retry: ...".
    let eventType = "message";
    const dataLines = [];
    for (const rawLine of block.split("\n")) {
      const line = rawLine.replace(/\r$/, "");
      if (!line) continue;
      if (line.startsWith(":")) continue; // comment
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") eventType = value;
      else if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n");
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      data = { raw };
    }
    emit(eventType, data);
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      // Events are separated by a blank line: "\n\n" (CRLF tolerated too).
      while ((idx = buffer.indexOf("\n\n")) !== -1 || (idx = buffer.indexOf("\r\n\r\n")) !== -1) {
        const sep = buffer.slice(idx, idx + 4) === "\r\n\r\n" ? 4 : 2;
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + sep);
        if (block.trim()) dispatch(block);
      }
    }
    // Flush any trailing block.
    const tail = buffer + decoder.decode();
    if (tail.trim()) dispatch(tail);
    emit("done", {});
  } catch (err) {
    if (signal && signal.aborted) {
      emit("done", { aborted: true });
      return;
    }
    emit("error", { message: String(err?.message || err) });
    throw err;
  }
}
