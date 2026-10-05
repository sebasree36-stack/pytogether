// What the server accepts from whom, and what a refusal costs the rest of the
// room. These are the rules behind the teacher's class panel.

import { checker, connect, joinAsGuest, setPermissions, sleep } from "./helpers.mjs";

export const name = "permissions";

export async function run(ctx) {
  const t = checker(name);
  const projectId = await ctx.newProject("permissions");

  const ana = await joinAsGuest(ctx, "Ana");
  const beto = await joinAsGuest(ctx, "Beto");

  const A = connect(ctx, "Ana", ana.access, projectId);
  const B = connect(ctx, "Beto", beto.access, projectId);
  await A.ready();
  await B.ready();

  t.check("a guest arrives with everything locked",
    A.permissions && !A.permissions.can_code && !A.permissions.can_draw && !A.permissions.can_chat);

  // A locked pupil typing: refused, and the room carries on without them
  A.act((_drawings, code) => code.insert(0, "Ana was here\n"));
  await sleep(1200);

  t.check("a locked pupil cannot type", A.refused.some((m) => m.action === "update"));
  t.check("the refused pupil is closed with 4010", A.closeCode === 4010);
  t.check("the other pupil in the room stays connected", B.closeCode === null);
  t.check("the other pupil's document is untouched", !B.code().includes("Ana was here"));

  const refusalsSoFar = A.refused.length;
  A.sendRaw({ type: "chat_message", message: "still here" });
  await sleep(600);
  t.check("nothing further is taken from a refused connection", A.refused.length === refusalsSoFar);

  // Chat is refused on its own, without closing the connection
  B.sendRaw({ type: "chat_message", message: "hola" });
  await sleep(600);
  t.check("a muted pupil cannot chat", B.refused.some((m) => m.action === "chat"));
  t.check("being muted does not close the connection", B.closeCode === null);

  // Unlocking reaches an open room without a reconnect
  await setPermissions(ctx, beto.id, { can_code: true, can_chat: true });
  await sleep(900);
  t.check("the teacher's change reaches an open room",
    B.permissions.can_code === true && B.permissions.can_chat === true);

  B.act((_drawings, code) => code.insert(0, "Beto was here\n"));
  await sleep(900);
  t.check("an unlocked pupil may type",
    B.closeCode === null && !B.refused.some((m) => m.action === "update"));

  // A payload that is not even base64
  const caro = await joinAsGuest(ctx, "Caro");
  const C = connect(ctx, "Caro", caro.access, projectId);
  await C.ready();
  C.sendRaw({ type: "update", update_b64: "not base64 at all !!" });
  await sleep(800);
  t.check("a malformed payload is refused", C.closeCode === 4010);

  // Drawing without permission to draw
  const dani = await joinAsGuest(ctx, "Dani", { can_code: true });
  const D = connect(ctx, "Dani", dani.access, projectId);
  await D.ready();
  D.act((drawings) => drawings.push([{
    type: "draw", color: "#fff", width: 2,
    points: [{ x: 1, y: 1 }, { x: 2, y: 2 }], author: dani.id,
  }]));
  await sleep(800);
  t.check("drawing is refused when only typing is allowed", D.closeCode === 4010);

  // The teacher is never restricted
  const T = connect(ctx, "teacher", ctx.teacher, projectId);
  await T.ready();
  t.check("the teacher arrives unrestricted",
    T.permissions.is_teacher && T.permissions.can_code && T.permissions.can_draw && T.permissions.can_chat);

  [A, B, C, D, T].forEach((client) => client.close());
  return t;
}
