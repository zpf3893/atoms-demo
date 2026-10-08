import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
class Element {
  constructor(){this.listeners={};this.children=[];this.dataset={};this.attributes={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.className='';this.style={setProperty(){}};this.classList={toggle(){}};}
  addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
  setAttribute(name,value){this.attributes[name]=value;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  focus(){}
  async emit(type){for(const callback of this.listeners[type]??[])await callback({preventDefault(){}});}
}
async function mount(id,saved={},now=Date.UTC(2026,8,29,9)){
  const html=fs.readFileSync(new URL(`../app/templates/${id}.html`, import.meta.url),'utf8');
  const elements=new Map();
  for(const match of html.matchAll(/<[\w-]+\b[^>]*\bid="([^"]+)"[^>]*>/g)){
    const node=new Element();node.value=match[0].match(/\bvalue="([^"]*)"/)?.[1]??'';node.disabled=/\bdisabled\b/.test(match[0]);elements.set(`#${match[1]}`,node);
  }
  const filters=['all','active','done'].map(filter=>{const el=new Element();el.dataset.filter=filter;return el;});
  const doc=new Element();doc.querySelector=selector=>{if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);};
  doc.querySelectorAll=selector=>selector==='[data-filter]'?filters:selector.startsWith('#expense-form')?['amount','category','date','note','add'].map(key=>elements.get(`#${key}`)):[];
  doc.createElement=()=>new Element();
  const clock={now},intervals=[],saves=[];
  class ClockDate extends Date{constructor(...args){super(...(args.length?args:[clock.now]));}static now(){return clock.now;}}
  const sandbox={document:doc,window:{demoStorage:{async load(){return structuredClone(saved);},async save(value){saves.push(structuredClone(value));}}},Date:ClockDate,crypto:{randomUUID},setInterval:callback=>intervals.push(callback),TextEncoder,console};
  vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1],sandbox);
  const tick=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
  await doc.emit('DOMContentLoaded');await tick();
  return {$:selector=>doc.querySelector(selector),filters,saves,clock,intervals,tick};
}
const tasks=await mount('tasks');
assert.equal(tasks.$('#add').disabled,false);
tasks.$('#task-input').value='完成可用的应用';tasks.$('#due-input').value='2026-09-30';await tasks.$('#task-form').emit('submit');await tasks.tick();
assert.equal(tasks.saves.at(-1).tasks.length,1);assert.equal(tasks.saves.at(-1).tasks[0].text,'完成可用的应用');
const checkbox=tasks.$('#task-list').children[0].children[0];checkbox.checked=true;await checkbox.emit('change');await tasks.tick();
assert.equal(tasks.saves.at(-1).tasks[0].done,true);
await tasks.filters[1].emit('click');assert.equal(tasks.$('#task-list').children.length,0);
const reloaded=await mount('tasks',tasks.saves.at(-1));assert.equal(reloaded.$('#done-count').textContent,1);
await reloaded.$('#task-list').children[0].children[2].emit('click');await reloaded.tick();assert.equal(reloaded.saves.at(-1).tasks.length,0);
console.log('PASS tasks: add, toggle, filtering, persisted reload, delete');
const expenses=await mount('expenses');expenses.$('#category').value='餐饮';expenses.$('#filter').value='all';
for(const value of ['0.10','0.20']){expenses.$('#amount').value=value;await expenses.$('#expense-form').emit('submit');await expenses.tick();}
assert.equal(expenses.$('#total').textContent,'¥ 0.30');assert.equal(expenses.saves.at(-1).expenses[0].cents,10);
const before=expenses.saves.length;expenses.$('#amount').value='1.234';await expenses.$('#expense-form').emit('submit');await expenses.tick();assert.equal(expenses.saves.length,before);
expenses.$('#filter').value='交通';await expenses.$('#filter').emit('change');assert.equal(expenses.$('#records').children.length,0);
expenses.$('#filter').value='all';await expenses.$('#filter').emit('change');await expenses.$('#records').children[0].children[3].emit('click');await expenses.tick();assert.equal(expenses.saves.at(-1).expenses.length,1);
console.log('PASS expenses: integer cents, sum, reject >2 decimals, filter, delete');
const focus=await mount('focus');focus.$('#work-minutes').value='1';focus.$('#break-minutes').value='1';await focus.$('#settings').emit('submit');await focus.tick();assert.equal(focus.$('#time').textContent,'01:00');
await focus.$('#toggle').emit('click');await focus.tick();focus.clock.now+=15000;await focus.$('#toggle').emit('click');await focus.tick();assert.equal(focus.saves.at(-1).remaining,45000);assert.equal(focus.saves.at(-1).running,false);
const paused=await mount('focus',focus.saves.at(-1),focus.clock.now+30000);assert.equal(paused.$('#time').textContent,'00:45');
await paused.$('#toggle').emit('click');await paused.tick();const running=paused.saves.at(-1);
const expired=await mount('focus',running,running.endAt+80000);await expired.tick();assert.equal(expired.saves.at(-1).sessions,1);assert.equal(expired.saves.at(-1).phase,'break');assert.equal(expired.saves.at(-1).running,false);
for(const callback of expired.intervals)callback();await expired.tick();assert.equal(expired.saves.at(-1).sessions,1);
await expired.$('#toggle').emit('click');await expired.tick();expired.clock.now+=60001;for(const callback of expired.intervals)callback();await expired.tick();assert.equal(expired.saves.at(-1).sessions,1);assert.equal(expired.saves.at(-1).phase,'work');
console.log('PASS focus: configure, start/pause, paused reload, expiry once, manual break, no duplicate focus sessions');
