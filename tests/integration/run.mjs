// Integration tests for the class mode, run against a live dev stack.
//
//   npm run dev          # in another terminal, and give it a moment
//   npm run test:integration
//
// They drive the real HTTP API and the real WebSocket, because every rule they
// cover lives in the consumer rather than in a function that can be called on
// its own. Nothing here is mocked.
//
// Each run builds its own throwaway class, uses the dev superuser test1 as the
// teacher, and deletes everything it made on the way out, pass or fail. If a
// run is killed mid-way, the next one clears the leftovers first.

import { API, django, request, sleep } from "./helpers.mjs";

const TEACHER = { email: "test1@gmail.com", password: "testtest" };
const CLASS_NAME = "__integration__";

const SUITES = ["./permissions.test.mjs", "./whiteboard.test.mjs"];

/** Remove any class this suite left behind, with its projects and its guests. */
function clearLeftovers() {
  django([
    "from usergroups.models import Group",
    "from users.models import User",
    "from utils.redis_helpers import SYNC_REDIS, ydoc_key",
    `for group in Group.objects.filter(group_name=${JSON.stringify(CLASS_NAME)}):`,
    "    guest_ids = list(group.group_members.filter(is_guest=True).values_list('id', flat=True))",
    "    for project_id in group.projects.values_list('id', flat=True):",
    "        SYNC_REDIS.delete(ydoc_key(project_id))",
    "    group.delete()",
    "    User.objects.filter(id__in=guest_ids).delete()",
  ].join("\n"));
}

async function main() {
  const reachable = await fetch(`${API}/api/me/`).then(() => true).catch(() => false);
  if (!reachable) {
    console.error(`Nothing is answering at ${API}. Start the stack with "npm run dev" first.`);
    process.exit(2);
  }

  clearLeftovers();

  const login = await request("POST", "/api/auth/token/", TEACHER);
  if (login.status !== 200) {
    console.error(`Could not sign in as ${TEACHER.email}. Is django-init done? (${login.status})`);
    process.exit(2);
  }

  const created = await request("POST", "/groups/create/", { group_name: CLASS_NAME }, login.data.access);
  if (created.status !== 201) {
    console.error(`Could not create the test class: ${created.status} ${JSON.stringify(created.data)}`);
    process.exit(2);
  }

  const ctx = {
    teacher: login.data.access,
    teacherId: String(created.data.owner_id),
    groupId: created.data.id,
    accessCode: created.data.access_code,
    async newProject(label) {
      const res = await request(
        "POST", `/groups/${created.data.id}/projects/create/`,
        { project_name: `${label}-${Date.now()}`, template: "none" }, login.data.access
      );
      if (res.status !== 201) throw new Error(`could not create project: ${res.status}`);
      return res.data.id;
    },
  };

  const summaries = [];
  try {
    for (const path of SUITES) {
      const suite = await import(path);
      console.log(`\n${suite.name}`);
      try {
        summaries.push(await suite.run(ctx));
      } catch (err) {
        console.log(`  FAIL  the suite threw: ${err.message}`);
        summaries.push({ suiteName: suite.name, failed: 1, results: [] });
      }
      // Let the sockets the suite closed finish unwinding on the server
      await sleep(500);
    }
  } finally {
    clearLeftovers();
  }

  const total = summaries.reduce((n, s) => n + s.results.length, 0);
  const failed = summaries.reduce((n, s) => n + s.failed, 0);
  console.log(`\n${total - failed}/${total} checks passed${failed ? `, ${failed} failed` : ""}`);
  process.exit(failed ? 1 : 0);
}

main();
