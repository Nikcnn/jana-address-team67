// Real-browser regression harness; no frontend testing packages or mock parser.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, rename, mkdir} from 'node:fs/promises';
import path from 'node:path';

const [base, chromePath, work] = process.argv.slice(2);
const profile = path.join(work, 'chrome-profile');
const downloads = path.join(work, 'downloads');
await mkdir(downloads, {recursive: true});
const chrome = spawn(chromePath, ['--headless', '--no-sandbox', '--disable-gpu',
  '--disable-background-networking', '--disable-component-update', '--no-first-run',
  '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  {stdio: 'ignore'});
let ws;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, description, timeout = 12000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const result = await check();
    if (result) return result;
    await delay(40);
  }
  throw new Error('Timed out: ' + description);
}
try {
  const port = await until(async () => {
    try { return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; }
    catch { return false; }
  }, 'Chrome DevTools startup');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map(), exceptions = [], requests = [];
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const waiting = pending.get(message.id);
      if (waiting) {
        clearTimeout(waiting.timer);
        pending.delete(message.id);
        if (message.error) waiting.reject(new Error(JSON.stringify(message.error)));
        else waiting.resolve(message.result);
      }
    } else if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    else if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 10000);
    pending.set(id, {resolve, reject, timer});
    ws.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const change = (selector, value) => evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    element.value = ${JSON.stringify(value)};
    element.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  const settled = () => until(() => evaluate("!document.getElementById('loadDemo').disabled"), 'request finished');
  const rowCount = () => evaluate("document.querySelectorAll('#resultsBody .data-row').length");
  async function loadFile(filename) {
    const {root} = await send('DOM.getDocument');
    const {nodeId} = await send('DOM.querySelector', {nodeId: root.nodeId, selector: '#fileInput'});
    await send('DOM.setFileInputFiles', {nodeId, files: [path.join(work, filename)]});
    await evaluate("document.getElementById('fileInput').dispatchEvent(new Event('change', {bubbles:true}))");
    await click('#runFile');
    await settled();
  }
  let lastDownload = 0;
  async function download(format, name) {
    assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
    assert.equal(await evaluate("document.getElementById('exportBtn').disabled || document.querySelector('.shell').inert"), false);
    // Pace the expanded suite: Chrome throttles rapid download bursts.
    await delay(Math.max(0, 250 - (Date.now() - lastDownload)));
    lastDownload = Date.now();
    await click(format === 'json' ? '#exportBtn' : '#exportCsvBtn');
    const source = path.join(downloads, `jana_address_output.${format}`);
    await until(async () => { try { await readFile(source); return true; } catch { return false; } }, 'download ' + name);
    await rename(source, path.join(work, name));
  }
  const open = async index => {
    await click(`tr[data-index="${index}"] .edit-row`);
    assert.equal(await evaluate("document.getElementById('drawer').getAttribute('aria-hidden')"), 'false');
  };
  const nullable = (key, isNull, value = '') => evaluate(`(() => {
    const toggle = document.getElementById(${JSON.stringify('null_' + key)});
    toggle.checked = ${isNull};
    toggle.dispatchEvent(new Event('change'));
    document.getElementById(${JSON.stringify('edit_' + key)}).value = ${JSON.stringify(value)};
  })()`);
  const submit = () => evaluate("document.getElementById('editForm').requestSubmit()");
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Network.enable');
  await send('Browser.setDownloadBehavior', {behavior: 'allow', downloadPath: downloads});
  await send('Emulation.setDeviceMetricsOverride', {width: 1365, height: 1000, deviceScaleFactor: 1, mobile: false});
  await send('Page.navigate', {url: base});
  await until(() => evaluate("document.getElementById('backendStatus')?.classList.contains('ok')"), 'UI health');
  const exportHelp = await evaluate("document.querySelector('.results-help').textContent");
  assert.ok(exportHelp.includes('ячейки с опасным префиксом защищены апострофом'));
  assert.ok(exportHelp.includes('JSON сохраняет значения точно, без изменений'));
  assert.ok(!exportHelp.includes('не запускайте формулы'));
  await loadFile('sample.json');
  assert.equal(await rowCount(), 4, await evaluate("document.getElementById('toast').textContent"));
  assert.equal(await evaluate("document.querySelectorAll('th[data-field]').length"), 14);
  assert.equal(await evaluate("document.querySelectorAll('#resultsBody td[data-field]').length"), 56);
  assert.equal(await evaluate("document.querySelector('tr[data-index=\"3\"] td[data-field=id]').textContent"), '900719925474099312345678901');
  assert.equal(await evaluate("document.querySelectorAll('#resultsBody img').length"), 0);
  assert.equal(await evaluate("window.__xss === undefined"), true);
  assert.equal(await evaluate("document.querySelector('tr[data-index=\"1\"] td[data-field=raw_address]').classList.contains('empty-value')"), true);
  assert.equal(await evaluate("document.querySelectorAll('#resultsBody .null-value').length > 0"), true);
  await download('json', 'baseline.json');
  console.log('PASS JSON upload: BOM, duplicate IDs, 14 fields, huge integer ID, Unicode, XSS, null vs empty');

  await change('#confidenceThreshold', '0');
  await click('[data-filter=review]');
  assert.equal(await rowCount(), 0);
  await change('#confidenceThreshold', '1');
  const belowOne = await evaluate("document.querySelectorAll('#resultsBody .needs-review').length");
  assert.ok(belowOne > 0);
  assert.equal(await rowCount(), belowOne);
  await click('[data-filter=all]');
  await change('#searchInput', 'dup');
  assert.equal(await rowCount(), 2);
  await change('#searchInput', '');
  console.log('PASS confidence threshold boundaries, highlights, search, original order');

  await open(1);
  assert.equal(await evaluate("document.getElementById('edit_id').readOnly && document.getElementById('edit_raw_address').readOnly"), true);
  assert.equal(await evaluate("document.querySelectorAll('#fieldsGroup textarea').length"), 11);
  await nullable('house', false, '');
  await submit();
  assert.ok(await evaluate("document.getElementById('editError').textContent.includes('непустую')"));
  await nullable('house', false, ' null ');
  await submit();
  assert.ok(await evaluate("document.getElementById('editError').textContent.length > 0"));
  await nullable('house', false, '12Ә/1');
  await nullable('landmark', false, '  Ә, "Қақпа"  ');
  await nullable('apartment', true);
  await submit();
  assert.equal(await evaluate("document.getElementById('drawer').getAttribute('aria-hidden')"), 'true');
  assert.equal(await evaluate("document.querySelector('tr[data-index=\"1\"] td[data-field=house]').textContent"), '12Ә/1');
  await click('[data-filter=edited]');
  assert.equal(await rowCount(), 1);
  assert.equal(await evaluate("document.querySelector('#resultsBody .data-row').dataset.index"), '1');
  await download('json', 'corrected.json');
  await download('csv', 'corrected.csv');
  await open(1);
  assert.ok(await evaluate("document.getElementById('original_house').textContent.includes('null')"));
  await click('#resetRow');
  assert.equal(await rowCount(), 0);
  await download('json', 'reset.json');
  await click('[data-filter=all]');
  await open(0);
  await submit();
  await click('[data-filter=edited]');
  assert.equal(await rowCount(), 0); // Saving without changes is not a correction.
  await click('[data-filter=all]');
  console.log('PASS 11 editable fields, atomic validation, occurrence-local edits/reset, JSON/CSV downloads');

  await loadFile('bad.jsonl');
  assert.equal(await rowCount(), 4);
  assert.ok(await evaluate("document.getElementById('toast').textContent.includes('HTTP 400')"));
  await loadFile('sample.jsonl');
  assert.equal(await rowCount(), 4);
  await download('json', 'jsonl.json');
  await loadFile('empty.json');
  assert.equal(await rowCount(), 0);
  await download('json', 'empty.json.export');
  console.log('PASS JSONL BOM/blank lines, malformed file keeps results, empty batch export');

  await click('#loadDemo');
  await settled();
  assert.ok(await rowCount() > 0);
  assert.ok(requests.some(url => url.endsWith('/api/v1/addresses/parse-batch')));
  assert.ok(requests.some(url => url.endsWith('/api/v1/addresses/upload')));
  assert.ok(requests.every(url => url.startsWith(base) || url.startsWith('blob:')),
    'External requests: ' + requests.filter(url => !url.startsWith(base)).join(','));
  console.log('PASS real demo + parse-batch, upload endpoint, no external page requests');

  await loadFile('sample.json');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  assert.equal(await evaluate("document.querySelector('.table-scroll').scrollWidth > document.querySelector('.table-scroll').clientWidth"), true);
  await open(0);
  await evaluate("document.getElementById('resetRow').focus()");
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9});
  assert.equal(await evaluate('document.activeElement.id'), 'closeDrawer');
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
  assert.equal(await evaluate("document.getElementById('drawer').inert"), true);
  console.log('PASS mobile containment with long Unicode, modal Tab trap and Escape');

  // Fault injection is limited to these negative tests; previous checks used real API.
  await evaluate(`window.realFetch = window.fetch; window.fetch = async () => new Response('{}', {status:200})`);
  await click('#loadDemo'); await settled();
  assert.equal(await rowCount(), 4);
  assert.ok(await evaluate("document.getElementById('toast').textContent.includes('Неизвестный формат')"));
  await evaluate(`window.fetch = async () => { throw new TypeError('network failure'); }`);
  await click('#loadDemo'); await settled();
  assert.equal(await rowCount(), 4);
  assert.ok(await evaluate("document.getElementById('toast').textContent.includes('API недоступен')"));
  await evaluate(`window.fetch = async () => { throw new DOMException('aborted', 'AbortError'); }`);
  await click('#loadDemo'); await settled();
  assert.equal(await rowCount(), 4);
  assert.ok(await evaluate("document.getElementById('toast').textContent.includes('Тайм-аут')"));
  await evaluate(`window.fetch = (...args) => new Promise(resolve => {
    window.finishRequest = () => resolve(new Response('{}', {status:200}));
  })`);
  await click('#loadDemo');
  assert.equal(await evaluate("document.getElementById('runFile').disabled && document.getElementById('selectFile').disabled && document.getElementById('exportBtn').disabled"), true);
  await evaluate('window.finishRequest()'); await settled();
  assert.equal(await rowCount(), 4);
  await evaluate(`(() => { window.fetch = window.realFetch;
    const transfer = new DataTransfer();
    transfer.items.add(new File(['[]'], '<img onerror=bad>.json', {type:'text/html'}));
    const input = document.getElementById('fileInput'); input.files=transfer.files;
    input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  assert.equal(await evaluate("document.getElementById('fileTitle').textContent"), '<img onerror=bad>.json');
  assert.equal(await evaluate("document.querySelectorAll('#fileTitle img').length"), 0);
  await evaluate(`(() => { const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(8*1024*1024+1)], 'huge.json'));
    const input = document.getElementById('fileInput'); input.files=transfer.files;
    input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  assert.equal(await evaluate("document.getElementById('runFile').disabled"), true);
  await evaluate(`(() => { window.fetch = window.realFetch;
    const transfer = new DataTransfer();
    transfer.items.add(new File(['payload'], '<img onerror=bad>.html', {type:'text/html'}));
    const input = document.getElementById('fileInput'); input.files=transfer.files;
    input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  assert.equal(await evaluate("document.getElementById('runFile').disabled"), true);
  assert.equal(await evaluate("document.querySelectorAll('#fileTitle img').length"), 0);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS negative schema/network/timeout tests, busy controls, safe filename/MIME, size/type rejection, no JS errors');

  // Synthetic export fixtures only; the parser-backed downloads above stay unchanged.
  const formulaRows = JSON.parse(await readFile(path.join(work, 'formula-input.json'), 'utf8'));
  await evaluate(`window.fetch = async () => new Response(${JSON.stringify(JSON.stringify(formulaRows))}, {status:200})`);
  await click('#loadDemo'); await settled();
  assert.equal(await rowCount(), formulaRows.length);
  await download('json', 'formula-before.json');
  await download('csv', 'formula.csv');
  await download('json', 'formula-after.json');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS synthetic CSV/JSON export fixtures downloaded before and after CSV');

  // Contract v1.1 fixtures mirror HTTP 200 item-level degradation, not error text.
  const mixedRows = JSON.parse(await readFile(path.join(work, 'mixed-response.json'), 'utf8'));
  const injectMixed = async (body = {items: mixedRows}, headers = {'X-Jana-Invalid-Items': '4'}) => {
    await evaluate(`window.fetch = async url => new Response(JSON.stringify(
      String(url).endsWith('/demo') ? ${JSON.stringify(mixedRows)} : ${JSON.stringify(body)}),
      {status:200, headers:${JSON.stringify(headers)}})`);
  };
  await injectMixed();
  await loadFile('mixed.json');
  assert.equal(await rowCount(), mixedRows.length);
  const banner = () => evaluate("({hidden:document.getElementById('invalidItemsNotice').hidden, text:document.getElementById('invalidItemsNotice').textContent})");
  assert.deepEqual(await banner(), {hidden:false, text:'4 записей входного файла невалидны и возвращены как пустые (confidence 0)'});
  const cellText = (index, key) => evaluate(`document.querySelector('tr[data-index="${index}"] td[data-field="${key}"]').textContent`);
  assert.ok((await cellText(1, 'id')).includes('—'));
  assert.ok((await cellText(1, 'id')).includes('нет id (невалидная запись)'));
  assert.ok((await cellText(2, 'raw_address')).includes('нет данных'));
  assert.ok((await cellText(2, 'raw_address')).includes('—'));
  for (const index of [1, 2, 3, 4, 6, 7]) {
    assert.ok(await evaluate(`document.querySelector('tr[data-index="${index}"] .invalid-badge')?.textContent === 'невалидная запись'`));
  }
  assert.equal(await evaluate("document.querySelector('tr[data-index=\"5\"] .invalid-badge') === null"), true);
  await download('json', 'mixed-baseline.json');
  await change('#confidenceThreshold', '0.5');
  await click('[data-filter=review]');
  assert.equal(await rowCount(), mixedRows.filter(row => row.confidence < 0.5).length);
  for (const index of [2, 3, 4, 6]) {
    assert.ok(await evaluate(`document.querySelector('tr[data-index="${index}"]') !== null`));
  }
  await click('[data-filter=all]');
  await open(3);
  assert.equal(await evaluate("document.getElementById('edit_id').readOnly && document.getElementById('edit_raw_address').readOnly"), true);
  assert.equal(await evaluate("document.getElementById('edit_id').value"), '');
  assert.equal(await evaluate("document.getElementById('edit_raw_address').value"), '');
  assert.ok(await evaluate("document.getElementById('drawerStatus').textContent.includes('Исходная запись невалидна')"));
  assert.equal(await evaluate("document.querySelectorAll('#fieldsGroup textarea').length"), 11);
  await nullable('house', false, '=1+1');
  await nullable('floor', false, '-1');
  await submit();
  assert.equal(await cellText(3, 'house'), '=1+1');
  assert.equal(await cellText(4, 'house'), 'null');
  assert.ok(await evaluate("document.querySelector('tr[data-index=\"3\"] .invalid-badge') !== null"));
  await download('json', 'mixed-edited.json');
  await download('csv', 'mixed-edited.csv');
  await open(3); await click('#resetRow');
  assert.equal(await cellText(3, 'house'), 'null');
  await download('json', 'mixed-reset.json');
  await injectMixed({items:mixedRows.slice(1)}); await loadFile('mixed.json');
  assert.equal(await rowCount(), mixedRows.length);
  assert.equal((await banner()).hidden, false); // Failed replacement retains the old batch's banner.
  assert.ok((await banner()).text.startsWith('4 '));
  // No header means no banner, even when id is null. Exercise each response envelope.
  for (const body of [mixedRows, {records:mixedRows}, {addresses:mixedRows}]) {
    await injectMixed(body, {}); await loadFile('mixed.json');
    assert.equal(await rowCount(), mixedRows.length);
    assert.deepEqual(await banner(), {hidden:true, text:''});
  }
  const badRows = [
    mixedRows.slice(1), [...mixedRows, mixedRows[0]],
    [{...mixedRows[0], extra:'unexpected'}, ...mixedRows.slice(1)],
    [{...mixedRows[0], id:true}, ...mixedRows.slice(1)],
    [{...mixedRows[0], id:1.5}, ...mixedRows.slice(1)],
    [{...mixedRows[0], id:[]}, ...mixedRows.slice(1)],
    [{...mixedRows[2], city:'not blank'}, ...mixedRows.slice(1)],
    [{...mixedRows[2], confidence:0.1}, ...mixedRows.slice(1)],
    [{...mixedRows[0], raw_address:42}, ...mixedRows.slice(1)],
    [{...mixedRows[0], house:12}, ...mixedRows.slice(1)],
    [{...mixedRows[0], city:''}, ...mixedRows.slice(1)],
    [{...mixedRows[0], city:'null'}, ...mixedRows.slice(1)],
    [{...mixedRows[0], confidence:'0'}, ...mixedRows.slice(1)],
    [{...mixedRows[6], city:'not blank'}, ...mixedRows.slice(1)],
  ];
  const missing = {...mixedRows[0]}; delete missing.floor;
  badRows.push([missing, ...mixedRows.slice(1)]);
  for (const body of badRows) {
    await injectMixed({items:body}); await loadFile('mixed.json');
    assert.equal(await rowCount(), mixedRows.length);
    assert.equal(await cellText(0, 'id'), 'valid');
    assert.equal(await evaluate("document.getElementById('toast').classList.contains('success')"), false);
    assert.deepEqual(await banner(), {hidden:true, text:''});
  }
  for (const header of ['<img src=x onerror=bad>', '-1', '1.5', '10001', '9']) {
    await injectMixed({items:mixedRows}, {'X-Jana-Invalid-Items':header});
    await loadFile('mixed.json');
    assert.equal(await rowCount(), mixedRows.length);
    assert.equal(await evaluate("document.getElementById('toast').classList.contains('success')"), false);
    assert.deepEqual(await banner(), {hidden:true, text:''});
    assert.equal(await evaluate("document.querySelectorAll('#invalidItemsNotice img, #toast img').length"), 0);
  }
  await injectMixed({items:mixedRows.slice(1)}, {});
  await click('#loadDemo'); await settled(); // Known demo input count must also match.
  assert.equal(await rowCount(), mixedRows.length);
  assert.equal(await evaluate("document.getElementById('toast').classList.contains('success')"), false);
  await download('json', 'mixed-rejected.json');
  // A mirrored single-record response may itself contain null identities.
  await injectMixed(mixedRows[3], {});
  await evaluate(`(() => { const transfer = new DataTransfer();
    transfer.items.add(new File([JSON.stringify({id:null, raw_address:null})], 'single.json'));
    const input = document.getElementById('fileInput'); input.files=transfer.files;
    input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  await click('#runFile'); await settled();
  assert.equal(await rowCount(), 1);
  assert.ok((await cellText(0, 'id')).includes('нет id'));
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS v1.1 mixed/null records, header banner, all envelopes, review filter, occurrence edits/reset, exports, malformed response preservation and cardinality');
} finally {
  if (ws) ws.close();
  chrome.kill('SIGTERM');
  await new Promise(resolve => { if (chrome.exitCode !== null) resolve(); else chrome.once('exit', resolve); });
}
