// Shared plumbing for the integration suites. See run.mjs for what they need.
//
// ws and yjs are borrowed from the frontend's node_modules rather than
// installed again: the suites talk to the same WebSocket the browser does, and
// a second copy of yjs is a second chance to test the wrong version.

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const NODE_MODULES = new URL("../../frontend/reactapp/node_modules/", import.meta.url);

export const WebSocket = (await import(new URL("ws/index.js", NODE_MODULES))).default;
export const Y = await import(new URL("yjs/dist/yjs.mjs", NODE_MODULES));

export const API = process.env.PYTOGETHER_API || "http://localhost:8000";
export const WS_HOST = process.env.PYTOGETHER_WS || "localhost:8000";

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** The user id inside a JWT, which is the only id the tests need. */
export const idOf = (token) =>
  String(JSON.parse(Buffer.from(token.split(".")[1], "base64").toString()).user_id);

export async function request(method, path, body, token) {
  const res = await fetch(API + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, data: await res.json().catch(() => null) };
}

/** Run a snippet in the Django container. Used for setup the API cannot do. */
export function django(snippet) {
  return execFileSync(
    "docker",
    ["compose", "-f", "docker-compose-dev.yaml", "exec", "-T", "django",
     "python", "manage.py", "shell", "-c", snippet],
    { cwd: REPO_ROOT, encoding: "utf8" }
  );
}

/**
 * One client in a project room, holding its own Y.Doc like the browser does.
 *
 * `act` makes a change, captures the delta Yjs emits for it and sends it, which
 * is exactly what PyIDE's update handler does.
 */
export function connect(ctx, label, token, projectId) {
  const doc = new Y.Doc();
  const client = { label, doc, refused: [], closeCode: null, permissions: null, synced: false };

  const ws = new WebSocket(
    `ws://${WS_HOST}/ws/groups/${ctx.groupId}/projects/${projectId}/code/?token=${token}`
  );
  client.ws = ws;

  ws.on("message", (raw) => {
    const msg = JSON.parse(raw);
    if (msg.type === "sync") {
      Y.applyUpdate(doc, Buffer.from(msg.ydoc_b64, "base64"), "server");
      client.synced = true;
    } else if (msg.type === "update") {
      Y.applyUpdate(doc, Buffer.from(msg.update_b64, "base64"), "server");
    } else if (msg.type === "permissions") {
      client.permissions = msg;
    } else if (msg.type === "refused") {
      client.refused.push(msg);
    }
  });
  ws.on("close", (code) => { client.closeCode = code; });
  ws.on("error", (err) => { client.error = err.message; });

  client.ready = async () => {
    for (let i = 0; i < 60 && !client.synced; i++) await sleep(50);
    if (!client.synced) throw new Error(`${label} never synced: ${client.error || "no reason given"}`);
    return client;
  };

  client.sendRaw = (payload) => ws.send(JSON.stringify(payload));

  client.act = (fn) => {
    let delta = null;
    const capture = (update, origin) => { if (origin !== "server") delta = update; };
    doc.on("update", capture);
    fn(doc.getArray("drawings"), doc.getText("codetext"), doc);
    doc.off("update", capture);
    if (delta) client.sendRaw({ type: "update", update_b64: Buffer.from(delta).toString("base64") });
    return delta;
  };

  client.drawings = () => doc.getArray("drawings").toArray();
  client.code = () => doc.getText("codetext").toString();
  client.close = () => ws.close();

  return client;
}

/** A fresh guest in the test class, with the permissions the suite wants. */
export async function joinAsGuest(ctx, name, permissions = {}) {
  const { status, data } = await request("POST", "/api/join-class/", {
    access_code: ctx.accessCode,
    name,
  });
  if (status !== 201) throw new Error(`could not join as ${name}: ${status} ${JSON.stringify(data)}`);

  const guest = { ...data, id: idOf(data.access) };
  if (Object.keys(permissions).length) {
    await setPermissions(ctx, guest.id, permissions);
  }
  return guest;
}

export async function setPermissions(ctx, userId, permissions) {
  const { status, data } = await request(
    "PUT", `/groups/${ctx.groupId}/permissions/`,
    { user_id: Number(userId), ...permissions }, ctx.teacher
  );
  if (status !== 200) throw new Error(`could not set permissions: ${status} ${JSON.stringify(data)}`);
}

/** A stroke shaped the way the client sends them. `author: null` leaves it unsigned. */
export const stroke = (author, overrides = {}) => ({
  type: "draw",
  color: "#FF6B6B",
  width: 2,
  points: [{ x: 1, y: 1 }, { x: 2, y: 2 }],
  ...(author === null ? {} : { author: String(author) }),
  ...overrides,
});

/** Keeps score so the runner can report and exit on the total. */
export function checker(suiteName) {
  const results = [];
  return {
    check(description, ok) {
      results.push({ description, ok });
      console.log(`  ${ok ? "ok  " : "FAIL"}  ${description}`);
      return ok;
    },
    get results() { return results; },
    get failed() { return results.filter((r) => !r.ok).length; },
    suiteName,
  };
}
