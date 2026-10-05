// Who may draw what on the shared board, and who may rub it out. Every stroke
// is signed, which is what makes these enforceable rather than merely hidden.

import { checker, connect, django, joinAsGuest, sleep, stroke } from "./helpers.mjs";

export const name = "whiteboard";

export async function run(ctx) {
  const t = checker(name);
  const projectId = await ctx.newProject("whiteboard");

  const T = connect(ctx, "teacher", ctx.teacher, projectId);
  await T.ready();
  T.act((drawings) => drawings.push([stroke(ctx.teacherId)]));
  await sleep(800);
  t.check("the teacher may draw", T.closeCode === null && T.refused.length === 0);

  const ana = await joinAsGuest(ctx, "Ana", { can_draw: true });
  const A = connect(ctx, "Ana", ana.access, projectId);
  await A.ready();

  A.act((drawings) => drawings.push([stroke(ana.id)]));
  await sleep(800);
  t.check("a pupil may draw a stroke signed with their own name",
    A.closeCode === null && A.refused.length === 0);
  t.check("the pupil sees the teacher's stroke as well", A.drawings().length === 2);

  A.act((drawings) => {
    for (let i = drawings.length - 1; i >= 0; i--) {
      if (drawings.get(i).author === ana.id) drawings.delete(i, 1);
    }
  });
  await sleep(800);
  t.check("a pupil may rub out their own stroke",
    A.closeCode === null && A.refused.length === 0);

  A.act((drawings) => {
    for (let i = drawings.length - 1; i >= 0; i--) {
      if (drawings.get(i).author === ctx.teacherId) drawings.delete(i, 1);
    }
  });
  await sleep(800);
  t.check("a pupil may not rub out the teacher's stroke", A.closeCode === 4010);

  // Signing with the teacher's name, byte-identical to a stroke already on the
  // board: the one that slipped through while strokes were compared as a set
  // rather than counted.
  const beto = await joinAsGuest(ctx, "Beto", { can_draw: true });
  const B = connect(ctx, "Beto", beto.access, projectId);
  await B.ready();
  B.act((drawings) => drawings.push([stroke(ctx.teacherId)]));
  await sleep(800);
  t.check("a pupil may not sign a stroke with another name", B.closeCode === 4010);

  const caro = await joinAsGuest(ctx, "Caro", { can_draw: true });
  const C = connect(ctx, "Caro", caro.access, projectId);
  await C.ready();
  C.act((drawings) => drawings.push([stroke(null)]));
  await sleep(800);
  t.check("an unsigned stroke is refused", C.closeCode === 4010);

  const dani = await joinAsGuest(ctx, "Dani", { can_draw: true });
  const D = connect(ctx, "Dani", dani.access, projectId);
  await D.ready();
  D.act((drawings) => drawings.push([stroke(dani.id, { type: "erase" })]));
  await sleep(800);
  t.check("a pupil may not use the rubber", D.closeCode === 4010);

  // Something that is not shaped like a stroke at all
  const eva = await joinAsGuest(ctx, "Eva", { can_draw: true });
  const E = connect(ctx, "Eva", eva.access, projectId);
  await E.ready();
  E.act((drawings) => drawings.push([
    { type: "draw", color: "x".repeat(500), width: 999, points: "nope", evil: 1, author: eva.id },
  ]));
  await sleep(800);
  t.check("a malformed stroke is refused", E.closeCode === 4010);

  // A stroke from before signing existed counts as the teacher's. Seeded
  // straight into Redis, because nothing can create one through the API now.
  const legacyProjectId = await ctx.newProject("whiteboard-legacy");
  django([
    "import y_py as Y",
    "from utils.redis_helpers import SYNC_REDIS, ydoc_key",
    "doc = Y.YDoc()",
    "text, strokes = doc.get_text('codetext'), doc.get_array('drawings')",
    "with doc.begin_transaction() as txn:",
    "    text.extend(txn, 'print(1)')",
    "    strokes.insert(txn, 0, {'type': 'draw', 'color': '#F8F9FA', 'width': 2,"
      + " 'points': [{'x': 5, 'y': 5}, {'x': 9, 'y': 9}]})",
    `SYNC_REDIS.set(ydoc_key(${legacyProjectId}), Y.encode_state_as_update(doc))`,
  ].join("\n"));

  const fran = await joinAsGuest(ctx, "Fran", { can_draw: true });
  const F = connect(ctx, "Fran", fran.access, legacyProjectId);
  await F.ready();
  t.check("the unsigned stroke is there to be found",
    F.drawings().length === 1 && F.drawings()[0].author === undefined);

  F.act((drawings) => drawings.delete(0, 1));
  await sleep(800);
  t.check("an unsigned stroke counts as the teacher's and a pupil cannot rub it out",
    F.closeCode === 4010);

  // The teacher clears everything, pupils' marks included
  const gaby = await joinAsGuest(ctx, "Gaby", { can_draw: true });
  const G = connect(ctx, "Gaby", gaby.access, projectId);
  await G.ready();
  G.act((drawings) => drawings.push([stroke(gaby.id)]));
  await sleep(900);

  const beforeClearing = T.drawings().length;
  T.act((drawings) => drawings.delete(0, drawings.length));
  await sleep(900);
  t.check(`the teacher may clear everyone's marks (${beforeClearing} before, ${T.drawings().length} after)`,
    T.closeCode === null && T.refused.length === 0 && T.drawings().length === 0);

  [T, A, B, C, D, E, F, G].forEach((client) => client.close());
  return t;
}
