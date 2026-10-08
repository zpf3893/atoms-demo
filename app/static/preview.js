// Isolated preview and per-project storage queues. No model output executes in the host.
const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'">`;
const STATIC_POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'">`;
export function staticPreview(html) {
  return '<!doctype html><html><head>' + STATIC_POLICY + '</head><body>' + html + '</body></html>';
}
export function createPreview(frame, api, onError, onSave) {
  let current = null;
  // Preserve queues when refreshing iframe or switching versions. A new frame's
  // load must wait for its predecessor's already accepted writes.
  const queues = new Map();
  const failedWrites = new Map();
  const queueFor = pid => queues.get(pid) || Promise.resolve();
  window.addEventListener('message', async event => {
    const data = event.data, context = current;
    if (!context || event.source !== frame.contentWindow || !data || data.channel !== context.channel) return;
    if (data.op === 'runtime-error') { onError(String(data.message || '').slice(0, 400)); return; }
    if (!['load', 'save'].includes(data.op) || typeof data.id !== 'string' || data.id.length > 40) return;
    try {
      let value;
      if (data.op === 'save') {
        if (!data.value || typeof data.value !== 'object' || Array.isArray(data.value) || new TextEncoder().encode(JSON.stringify(data.value)).length > 20000) throw new Error('应用数据须为20KB以内的对象');
        if (context.readonly) throw new Error('已归档项目不能修改数据，请先恢复项目');
        onSave('正在保存数据…');
        const save = () => api(`/projects/${context.pid}/state`, { method: 'PUT', body: JSON.stringify({ value: data.value }) });
        const job = queueFor(context.pid).then(save, save);
        // Keep the chain usable after a failed write, but never let an export or
        // navigation silently treat that failed write as successfully saved.
        queues.set(context.pid, job.then(() => failedWrites.delete(context.pid), error => failedWrites.set(context.pid, error)));
        value = await job;
        if (context === current) onSave('应用数据已保存');
      } else { await queueFor(context.pid); value = await api(`/projects/${context.pid}/state`); }
      if (context === current) event.source.postMessage({ channel: context.channel, id: data.id, value }, '*');
    } catch (error) {
      if (context === current) {
        onSave('数据保存或读取失败');
        event.source.postMessage({ channel: context.channel, id: data.id, error: error.message }, '*');
      }
    }
  });
  return {
    async flush(pid) {
      await queueFor(pid);
      if (failedWrites.has(pid)) throw new Error('应用数据尚未保存成功，请先在预览内重试保存。' + failedWrites.get(pid).message);
    },
    clear() { current = null; frame.removeAttribute('srcdoc'); },
    render(project) {
      const channel = crypto.randomUUID(); current = { channel, pid: project.id, readonly: project.archived };
      const bridge = `<script>(()=>{const channel=${JSON.stringify(channel)};let next=0;const pending=new Map();
      const request=(op,value)=>new Promise((resolve,reject)=>{const id=String(++next);const timer=setTimeout(()=>{pending.delete(id);reject(new Error('数据服务暂时无响应'))},15000);pending.set(id,{resolve,reject,timer});parent.postMessage({channel,id,op,value},'*')});
      window.addEventListener('message',e=>{if(e.source!==parent||e.data?.channel!==channel||!pending.has(e.data.id))return;const p=pending.get(e.data.id);clearTimeout(p.timer);pending.delete(e.data.id);e.data.error?p.reject(new Error(e.data.error)):p.resolve(e.data.value)});
      Object.defineProperty(window,'demoStorage',{value:Object.freeze({load:()=>request('load'),save:value=>request('save',value)}),writable:false});
      const report=text=>parent.postMessage({channel,op:'runtime-error',message:String(text).slice(0,400)},'*');
      window.addEventListener('error',e=>report(e.message));window.addEventListener('unhandledrejection',e=>report(e.reason?.message||'未处理的异步错误'));
      })();<\/script>`;
      frame.srcdoc = '<!doctype html><html><head>' + POLICY + bridge + '</head><body>' + project.html + '</body></html>';
    },
  };
}
