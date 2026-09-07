import "./test-env.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { freshWindow, teardown } from "./dom-setup.js";
import { init } from "../test-build/job-status.js";

function wait(window, ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function lineTexts() {
  return [...document.querySelectorAll("#job-lines .log-line")].map((el) => el.textContent);
}

function setup(status) {
  document.body.innerHTML = `
    <span id="job-status">${status}</span>
    <span id="job-elapsed"></span>
    <div id="job-lines"></div>
    <p id="job-error"></p>
    <a class="button" href="/x">View</a>
  `;
}

test("a running job polls the status endpoint and updates the DOM in place", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ id: "abc123", status: "running", lines: ["line one", "line two"], error: null }),
  });

  init();
  await wait(window, 50); // let the first poll() round-trip resolve

  assert.equal(document.getElementById("job-status").textContent, "running");
  assert.deepEqual(lineTexts(), ["line one", "line two"]);

  teardown(window);
});

test("starts the elapsed timer immediately for a running job", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  globalThis.fetch = async () => new Promise(() => {}); // never resolves; only the timer matters here

  init();

  assert.equal(document.getElementById("job-elapsed").textContent, "(still running, 0:00 elapsed)");

  teardown(window);
});

test("reloads the page once the job reaches a terminal state", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ id: "abc123", status: "succeeded", lines: [], error: null }),
  });
  let reloaded = 0;
  window.location.reload = () => {
    reloaded++;
  };

  init();
  await wait(window, 50);

  assert.equal(reloaded, 1);

  teardown(window);
});

test("retries after a non-ok response instead of giving up", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls === 1) return { ok: false };
    return {
      ok: true,
      json: async () => ({ id: "abc123", status: "running", lines: ["retried ok"], error: null }),
    };
  };

  init();
  await wait(window, 1700); // past one failed attempt + one 1.5s retry delay

  assert.equal(calls, 2);
  assert.deepEqual(lineTexts(), ["retried ok"]);

  teardown(window);
});

test("does not touch #job-lines DOM when polled content is unchanged", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ id: "abc123", status: "running", lines: ["same line"], error: null }),
  });

  init();
  await wait(window, 50); // first poll renders "same line"

  const linesEl = document.getElementById("job-lines");
  const nodeBeforeSecondPoll = linesEl.firstChild;

  await wait(window, 1600); // second poll, identical content

  // Re-assigning innerHTML with the same string still tears down and
  // recreates child nodes, which would clear any in-progress text selection —
  // an unchanged poll must leave the existing nodes alone.
  assert.equal(linesEl.firstChild, nodeBeforeSecondPoll);

  teardown(window);
});

test("an appended or last-line-mutated poll only touches that one line, not earlier lines", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  const responses = [
    ["line one"],
    ["line one", "line two"], // append
    ["line one", "line two updated"], // mutate the last line in place (spinner/sub-log growth)
  ];
  let call = 0;
  globalThis.fetch = async () => {
    const lines = responses[Math.min(call, responses.length - 1)];
    call++;
    return { ok: true, json: async () => ({ id: "abc123", status: "running", lines, error: null }) };
  };

  init();
  await wait(window, 50); // poll 1: ["line one"]

  const linesEl = document.getElementById("job-lines");
  const firstLineNode = linesEl.children[0];

  await wait(window, 1600); // poll 2: append "line two"
  assert.equal(linesEl.children.length, 2);
  assert.equal(linesEl.children[0], firstLineNode, "earlier line's node must survive an append");
  const secondLineNode = linesEl.children[1];

  await wait(window, 1600); // poll 3: mutate the last line in place
  assert.equal(linesEl.children[0], firstLineNode, "earlier line's node must survive a last-line mutation");
  assert.equal(linesEl.children[1], secondLineNode, "the mutated line keeps its node, just new content");
  assert.equal(linesEl.children[1].textContent, "line two updated");

  teardown(window);
});

test("renders job.error into #job-error even while still running", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("running");
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ id: "abc123", status: "running", lines: [], error: "<b>warning</b>" }),
  });

  init();
  await wait(window, 50);

  assert.equal(document.getElementById("job-error").innerHTML, "<b>warning</b>");

  teardown(window);
});

test("a terminal-state page (already succeeded server-side) does not poll", async () => {
  const window = freshWindow("http://localhost/jobs/abc123");
  setup("succeeded");
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return { ok: true, json: async () => ({ id: "abc123", status: "succeeded", lines: [], error: null }) };
  };

  init();
  await wait(window, 50);

  assert.equal(calls, 0);

  teardown(window);
});
