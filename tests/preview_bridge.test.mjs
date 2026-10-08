// Run with: node --test tests/preview_bridge.test.mjs
// This exercises the real bridge without a browser, packages, HTTP or .env.
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { setImmediate } from 'node:timers/promises';
import test from 'node:test';

// A data URL loads this ES module even though the Python project has no
// package.json declaring a module type for its browser-side .js files.
const source = await readFile(new URL('../app/static/preview.js', import.meta.url), 'utf8');
const { createPreview } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const project = { id: 'project-one', html: '<html><head></head><body>Preview</body></html>', archived: false };

function temporaryGlobal(t, name, value) {
  const original = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { value, configurable: true });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, name, original);
    else delete globalThis[name];
  });
}

function harness(t, api) {
  let receive;
  temporaryGlobal(t, 'window', {
    addEventListener(name, callback) {
      assert.equal(name, 'message');
      receive = callback;
    },
  });
  if (!globalThis.crypto?.randomUUID) temporaryGlobal(t, 'crypto', webcrypto);
  const replies = [], statuses = [], runtimeErrors = [];
  const frame = {
    // An iframe's WindowProxy identity remains stable when srcdoc changes.
    contentWindow: { postMessage(message) { replies.push(structuredClone(message)); } },
    removeAttribute(name) { if (name === 'srcdoc') delete this.srcdoc; },
  };
  const preview = createPreview(frame, api, message => runtimeErrors.push(message), message => statuses.push(message));
  const channel = () => JSON.parse(frame.srcdoc.match(/const channel=("[^"]+")/)[1]);
  const send = (data, source = frame.contentWindow) => receive({
    source, data: { channel: channel(), id: 'request-one', ...data },
  });
  preview.render(project);
  return { preview, frame, replies, statuses, runtimeErrors, channel, send };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('failed persistence blocks flush instead of silently losing a write', async t => {
  const h = harness(t, async () => { throw new Error('保存服务暂时不可用'); });
  await h.send({ op: 'save', value: { tasks: ['尚未保存'] } });

  assert.equal(h.replies[0].error, '保存服务暂时不可用');
  assert.equal(h.statuses.at(-1), '数据保存或读取失败');
  await assert.rejects(h.preview.flush(project.id), /应用数据尚未保存成功.*保存服务暂时不可用/);
});

test('a successful retry clears the previous failure and saves the latest value', async t => {
  let attempts = 0, stored;
  const h = harness(t, async (path, options) => {
    assert.equal(path, `/projects/${project.id}/state`);
    assert.equal(options.method, 'PUT');
    if (++attempts === 1) throw new Error('暂时断开连接');
    stored = JSON.parse(options.body).value;
    return stored;
  });
  await h.send({ id: 'first', op: 'save', value: { tasks: ['旧内容'] } });
  await assert.rejects(h.preview.flush(project.id));
  await h.send({ id: 'retry', op: 'save', value: { tasks: ['最新内容'] } });

  await h.preview.flush(project.id);
  assert.equal(attempts, 2);
  assert.deepEqual(stored, { tasks: ['最新内容'] });
  assert.deepEqual(h.replies.at(-1).value, stored);
  assert.equal(h.replies.at(-1).error, undefined);
  assert.equal(h.statuses.at(-1), '应用数据已保存');
});

test('wrong message sources and old channels cannot read, write or report errors', async t => {
  const calls = [];
  const h = harness(t, async path => { calls.push(path); return { ok: true }; });
  const oldChannel = h.channel();
  h.preview.render(project);
  assert.notEqual(h.channel(), oldChannel);

  await h.send({ op: 'load' }, {});
  await h.send({ op: 'save', value: {} }, {});
  await h.send({ op: 'runtime-error', message: 'spoofed error' }, {});
  await h.send({ channel: oldChannel, op: 'load' });
  await h.send({ channel: oldChannel, op: 'save', value: {} });
  await h.send({ channel: oldChannel, op: 'runtime-error', message: 'stale error' });
  assert.deepEqual(calls, []);
  assert.deepEqual(h.replies, []);
  assert.deepEqual(h.runtimeErrors, []);

  // The current iframe still works after rejected messages.
  await h.send({ op: 'load' });
  assert.deepEqual(calls, [`/projects/${project.id}/state`]);
  assert.deepEqual(h.replies[0].value, { ok: true });
});

test('refreshing the same project keeps accepted writes ordered before the new frame loads', async t => {
  const firstWrite = deferred(), secondWrite = deferred(), calls = [];
  let stored = {}, writes = 0;
  const h = harness(t, async (path, options) => {
    assert.equal(path, `/projects/${project.id}/state`);
    if (options?.method === 'PUT') {
      const value = JSON.parse(options.body).value;
      calls.push(`save:${value.revision}`);
      await (++writes === 1 ? firstWrite.promise : secondWrite.promise);
      stored = value;
    } else calls.push('load');
    return structuredClone(stored);
  });

  const oldChannel = h.channel();
  const oldSave = h.send({ id: 'old-save', op: 'save', value: { revision: 1 } });
  await setImmediate();
  assert.deepEqual(calls, ['save:1']);

  h.preview.render({ ...project, html: project.html.replace('Preview', 'Refreshed') });
  const newChannel = h.channel();
  assert.notEqual(newChannel, oldChannel);
  const newSave = h.send({ id: 'new-save', op: 'save', value: { revision: 2 } });
  const newLoad = h.send({ id: 'new-load', op: 'load' });
  let flushed = false;
  const flush = h.preview.flush(project.id).then(() => { flushed = true; });
  await setImmediate();
  assert.deepEqual(calls, ['save:1']);
  assert.equal(flushed, false);

  firstWrite.resolve();
  await oldSave;
  await setImmediate();
  assert.deepEqual(calls, ['save:1', 'save:2']);
  assert.equal(flushed, false);
  assert.equal(h.replies.length, 0, 'the old frame must not receive a reply in the new document');

  secondWrite.resolve();
  await Promise.all([newSave, newLoad, flush]);
  assert.deepEqual(calls, ['save:1', 'save:2', 'load']);
  assert.equal(flushed, true);
  assert.deepEqual(h.replies.map(reply => [reply.channel, reply.id, reply.value]), [
    [newChannel, 'new-save', { revision: 2 }],
    [newChannel, 'new-load', { revision: 2 }],
  ]);
});

test('archived previews can load data but cannot write it', async t => {
  const calls = [];
  const h = harness(t, async (path, options) => {
    calls.push(options?.method || 'GET');
    return { tasks: ['保留已有内容'] };
  });
  h.preview.render({ ...project, archived: true });
  await h.send({ id: 'read', op: 'load' });
  await h.send({ id: 'write', op: 'save', value: { tasks: [] } });

  assert.deepEqual(calls, ['GET']);
  assert.deepEqual(h.replies[0].value, { tasks: ['保留已有内容'] });
  assert.match(h.replies[1].error, /已归档项目不能修改数据/);
  await h.preview.flush(project.id);
});
