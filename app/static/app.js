(() => {
  'use strict';
  const fields = [
    ['province','Область / город республиканского значения'],['city','Город'],['district','Район'],['living_area','Жилой массив / ЖК'],
    ['street_type','Тип улицы'],['street_name','Название улицы'],['house','Дом'],['apartment','Квартира'],['entrance','Подъезд'],['floor','Этаж'],['landmark','Ориентир']
  ];
  const $=id=>document.getElementById(id);
  const keys = ['id', 'raw_address', ...fields.map(([key]) => key), 'confidence'];
  document.querySelector('.results-help').textContent = 'Порядок входа сохранён, одинаковые ID — отдельные строки. JSON/CSV экспортируют все записи, независимо от фильтра. CSV: null — пустая ячейка; ячейки с опасным префиксом защищены апострофом. JSON сохраняет значения точно, без изменений.';
  // Occurrence index owns every row: duplicate IDs never share edits or resets.
  const state = {file: null, results: [], original: [], edits: new Set(),
    activeIndex: null, filter: 'all', busy: false, loaded: false, returnFocus: null};
  const requestTimeout = 60000;
  function notice(msg,success=false){const x=$('toast');x.textContent=msg;x.className='toast show'+(success?' success':'');}
  function hideNotice(){$('toast').className='toast';}
  async function requestJSON(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), requestTimeout);
    try {
      const response = await fetch(url, {...options, signal: controller.signal});
      if (!response.ok) {
        // Do not echo response bodies, filenames or addresses in error messages.
        throw new Error(`HTTP ${response.status}. Проверьте формат и лимиты файла.`);
      }
      // Preserve integer IDs beyond Number.MAX_SAFE_INTEGER without rounding.
      const body = JSON.parse(await response.text(), (key, value, context) => {
        if (key === 'id' && typeof value === 'number' && !Number.isSafeInteger(value)) {
          if (!context?.source || !/^-?\d+$/.test(context.source)) {
            throw new Error('Для точного чтения больших числовых ID обновите браузер.');
          }
          return BigInt(context.source);
        }
        return value;
      });
      const header = response.headers.get('X-Jana-Invalid-Items');
      const invalidItems = header === null ? 0 : Number(header);
      if (header !== null && (!/^\d+$/.test(header) || !Number.isSafeInteger(invalidItems) || invalidItems > 10000)) {
        throw new Error('API вернул некорректное число невалидных записей. Предыдущие результаты сохранены.');
      }
      return {body, invalidItems};
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Тайм-аут API (60 с). Повторите запрос.');
      if (error instanceof SyntaxError) throw new Error('API вернул некорректный JSON.');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  function setBusy(busy) {
    state.busy = busy;
    $('runFile').disabled = busy || !state.file;
    $('loadDemo').disabled = busy;
    $('selectFile').disabled = busy;
    $('fileInput').disabled = busy;
    $('dropzone').setAttribute('aria-disabled', String(busy));
    for (const id of ['exportBtn', 'exportCsvBtn']) $(id).disabled = busy || !state.loaded;
  }
  function unpack(body) {
    if (Array.isArray(body)) return body;
    if (body && typeof body === 'object') {
      for (const key of ['items', 'records', 'addresses']) {
        if (Array.isArray(body[key])) return body[key];
      }
      if (Object.hasOwn(body, 'raw_address')) return [body];
    }
    throw new Error('Неизвестный формат ответа API. Предыдущие результаты сохранены.');
  }
  function blankResult(row) {
    return row.confidence === 0 && fields.every(([key]) => row[key] === null);
  }
  function invalidInput(row) {
    return row.id === null || row.raw_address === null || [...row.raw_address].length > 3000;
  }
  function validateResults(rows, expectedCount = null) {
    if (rows.length > 10000) throw new Error('API превысил лимит 10 000 записей.');
    if (expectedCount !== null && rows.length !== expectedCount) {
      throw new Error('Число записей API не совпадает с числом входных. Предыдущие результаты сохранены.');
    }
    for (const [index, row] of rows.entries()) {
      const valid = row && typeof row === 'object' && !Array.isArray(row)
        && Object.keys(row).length === keys.length
        && keys.every(key => Object.hasOwn(row, key))
        && (row.id === null || typeof row.id === 'string' || typeof row.id === 'bigint' || Number.isSafeInteger(row.id))
        && (row.raw_address === null ? blankResult(row) :
          typeof row.raw_address === 'string' && ([...row.raw_address].length <= 3000 || blankResult(row)))
        && fields.every(([key]) => row[key] === null ||
          (typeof row[key] === 'string' && row[key].trim() !== '' && row[key].trim().toLowerCase() !== 'null'))
        && Number.isFinite(row.confidence) && row.confidence >= 0 && row.confidence <= 1;
      if (!valid) throw new Error(`Некорректная запись API №${index + 1}. Предыдущие результаты сохранены.`);
    }
  }
  async function run(load) {
    if (state.busy) return;
    hideNotice();
    setBusy(true);
    const start = performance.now();
    try {
      const {body, invalidItems, expectedCount = null} = await load();
      const rows = unpack(body);
      validateResults(rows, expectedCount);
      if (invalidItems > rows.length) throw new Error('API вернул некорректное число невалидных записей. Предыдущие результаты сохранены.');
      closeRow(false);
      state.results = structuredClone(rows);
      state.original = structuredClone(rows);
      state.edits.clear();
      state.loaded = true;
      state.filter = 'all';
      $('searchInput').value = '';
      updateFilters();
      $('timeCount').textContent = (performance.now() - start).toFixed(0) + ' мс';
      updateStats();
      render();
      $('invalidItemsNotice').textContent = invalidItems > 0 ?
        `${invalidItems} записей входного файла невалидны и возвращены как пустые (confidence 0)` : '';
      $('invalidItemsNotice').hidden = invalidItems === 0;
      notice(`Обработано ${rows.length} записей. Правки хранятся только в этой вкладке; сохраните экспорт.`, true);
    } catch (error) {
      // Browser/network exceptions can include arbitrary input; only our messages are shown.
      notice(error instanceof TypeError ? 'API недоступен. Предыдущие результаты сохранены.' : error.message);
    } finally {
      setBusy(false);
    }
  }
  $('selectFile').addEventListener('click',e=>{e.stopPropagation();$('fileInput').click()});
  $('dropzone').addEventListener('click',e=>{if(!state.busy&&e.target!==$('fileInput'))$('fileInput').click()});
  $('dropzone').addEventListener('keydown',e=>{if(e.target===$('dropzone')&&!state.busy&&(e.key==='Enter'||e.key===' ')){e.preventDefault();$('fileInput').click()}});
  for(const evt of ['dragover','dragenter'])$('dropzone').addEventListener(evt,e=>{e.preventDefault();$('dropzone').classList.add('dragover')});
  for(const evt of ['dragleave','drop'])$('dropzone').addEventListener(evt,e=>{e.preventDefault();$('dropzone').classList.remove('dragover')});
  $('dropzone').addEventListener('drop',e=>{if(e.dataTransfer.files?.length)setFile(e.dataTransfer.files[0])});
  $('fileInput').addEventListener('change',e=>{if(e.target.files.length)setFile(e.target.files[0])});
  function setFile(file) {
    if (state.busy) return;
    hideNotice();
    state.file = null;
    $('fileTitle').textContent = 'Выберите JSON/JSONL';
    $('fileHint').textContent = '.json / .jsonl · максимум 8 МБ';
    setBusy(false);
    if (!/\.(json|jsonl)$/i.test(file.name)) {
      notice('Поддерживаются только файлы .json и .jsonl.');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      notice('Файл должен быть не более 8 МБ.');
      return;
    }
    state.file = file;
    $('fileTitle').textContent = file.name;
    $('fileHint').textContent = (file.size / 1024).toFixed(1) + ' КБ · готов к обработке';
    setBusy(false);
  }
  async function fileRecordCount(file) {
    const text = (await file.text()).replace(/^\ufeff/, '');
    try {
      if (/\.jsonl$/i.test(file.name)) {
        return text.split(/\r?\n/).filter(line => line.trim() !== '').map(line => JSON.parse(line)).length;
      }
      const body = JSON.parse(text);
      if (Array.isArray(body)) return body.length;
      if (body && typeof body === 'object') {
        for (const key of ['items', 'records', 'addresses']) {
          if (Array.isArray(body[key])) return body[key].length;
        }
        if (Object.hasOwn(body, 'raw_address')) return 1;
      }
      return null; // Unknown envelopes remain the server's responsibility.
    } catch (error) {
      if (error instanceof SyntaxError) return null; // Send malformed JSON to the API as before.
      throw error;
    }
  }
  $('runFile').addEventListener('click', () => {
    if (!state.file || state.busy) return;
    const file = state.file;
    run(async () => {
      const expectedCount = await fileRecordCount(file);
      const form = new FormData();
      form.append('file', file);
      return {...await requestJSON('/api/v1/addresses/upload', {method: 'POST', body: form}), expectedCount};
    });
  });
  $('loadDemo').addEventListener('click', () => run(async () => {
    const {body} = await requestJSON('/api/v1/addresses/demo');
    const items = unpack(body);
    return {...await requestJSON('/api/v1/addresses/parse-batch', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: serializeRows(items)}), expectedCount: items.length};
  }));
  function threshold() { return Number($('confidenceThreshold').value); }
  function updateStats() {
    const rows = state.results;
    const low = rows.filter(row => row.confidence < threshold()).length;
    $('totalCount').textContent = rows.length.toLocaleString('ru-RU');
    $('highCount').textContent = rows.length - low;
    $('reviewCount').textContent = low;
    $('resultsBadge').textContent = rows.length;
    $('thresholdValue').textContent = threshold().toFixed(2);
  }
  function shown(){const q=$('searchInput').value.trim().toLowerCase();return state.results.map((v,i)=>({v,i})).filter(({v,i})=>{
    if(state.filter==='review'&&v.confidence>=threshold())return false;if(state.filter==='edited'&&!state.edits.has(i))return false;
    return !q||String(v.id??'').toLowerCase().includes(q)||String(v.raw_address??'').toLowerCase().includes(q)||String(v.city??'').toLowerCase().includes(q)||String(v.street_name??'').toLowerCase().includes(q)
  })}
  function make(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
  function displayValue(value) {
    return value === null ? 'null' : value === '' ? '«» (пустая строка)' : String(value);
  }
  function render() {
    const data = shown(), tbody = $('resultsBody');
    tbody.replaceChildren();
    $('visibleCount').textContent = `Показано ${data.length} из ${state.results.length}`;
    if (!data.length) {
      const tr = make('tr'), td = make('td', 'empty-cell');
      td.colSpan = 15;
      td.append(make('strong', '', state.loaded ? 'Нет строк по текущему фильтру' : 'Здесь появятся ваши адреса'));
      tr.append(td);
      tbody.append(tr);
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const {v, i} of data) {
      const tr = make('tr', 'data-row' + (v.confidence < threshold() ? ' needs-review' : ''));
      tr.dataset.index = i;
      const invalid = invalidInput(state.original[i]);
      if (invalid) tr.classList.add('invalid-record');
      const action = make('td'), button = make('button', 'edit-row', `${i + 1} · Править`);
      button.type = 'button';
      button.setAttribute('aria-label', `Править запись №${i + 1}`);
      button.addEventListener('click', () => openRow(i));
      action.append(button, make('span', 'row-state', state.edits.has(i) ? 'Изменено вручную' : 'Результат парсера'));
      if (invalid) action.append(make('span', 'invalid-badge', 'невалидная запись'));
      tr.append(action);
      for (const key of keys) {
        const changed = v[key] !== state.original[i][key];
        const td = make('td', (key === 'raw_address' ? 'addr-value ' : '') + (changed ? 'cell-edited' : ''));
        td.dataset.field = key;
        if (v[key] === null && (key === 'id' || key === 'raw_address')) {
          td.append(make('span', '', '—'), make('small', 'missing-data',
            key === 'id' ? 'нет id (невалидная запись)' : 'нет данных'));
        } else {
          td.textContent = key === 'confidence' ? v[key].toFixed(2) : displayValue(v[key]);
        }
        if (v[key] === null) td.classList.add('null-value');
        if (v[key] === '') td.classList.add('empty-value');
        if (changed) td.title = 'Результат парсера: ' + displayValue(state.original[i][key]);
        tr.append(td);
      }
      fragment.append(tr);
    }
    tbody.append(fragment);
  }
  $('searchInput').addEventListener('input',render);
  function updateFilters() {
    document.querySelectorAll('.filter').forEach(button => {
      const active = button.dataset.filter === state.filter;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    updateFilters();
    render();
  }));
  $('confidenceThreshold').addEventListener('input', () => { updateStats(); render(); });
  function serializeRows(rows) {
    return '[\n' + rows.map(row => '  {' + Object.entries(row).map(([key, value]) =>
      JSON.stringify(key) + ': ' + (key === 'id' && typeof value === 'bigint' ? value.toString() : JSON.stringify(value)))
      .join(', ') + '}').join(',\n') + '\n]';
  }
  function csvCell(value) {
    let text = value === null ? '' : String(value);
    if (typeof value === 'string') {
      // Compare the whole match: JavaScript $ also accepts a trailing newline.
      const integer = /^-?\d+$/.exec(value)?.[0] === value;
      if (/^[=+@\t\r]/.test(value) || (value.startsWith('-') && !integer)) text = "'" + text;
    }
    // Quote every CSV cell, double embedded quotes. Null is an empty cell.
    return '"' + text.replaceAll('"', '""') + '"';
  }
  function download(format) {
    const rows = state.results.map(row => Object.fromEntries(keys.map(key => [key, row[key]])));
    const text = format === 'json' ? serializeRows(rows) + '\n' :
      '\ufeff' + [keys, ...rows.map(row => keys.map(key => row[key]))]
        .map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
    const blob = new Blob([text], {type: format === 'json' ? 'application/json;charset=utf-8' : 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob), anchor = make('a');
    anchor.href = url;
    anchor.download = `jana_address_output.${format}`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('exportBtn').addEventListener('click', () => download('json'));
  $('exportCsvBtn').addEventListener('click', () => download('csv'));
  function openRow(index) {
    if (state.busy) return;
    state.activeIndex = index;
    state.returnFocus = document.activeElement;
    const row = state.results[index];
    $('edit_id').value = row.id === null ? '' : String(row.id);
    $('edit_id').placeholder = row.id === null ? '— · нет id (невалидная запись)' : '';
    $('edit_raw_address').value = row.raw_address ?? '';
    $('edit_raw_address').placeholder = row.raw_address === null ? '— · нет данных' : '';
    $('drawerStatus').textContent = `Запись №${index + 1} · Confidence: ${row.confidence.toFixed(2)} · эвристика, не точность · ` +
      (state.edits.has(index) ? 'Изменено вручную' : 'Результат парсера') +
      (invalidInput(state.original[index]) ? ' · Исходная запись невалидна. Можно исправить 11 компонентов; id и исходный адрес не меняются.' : '');
    $('editError').textContent = '';
    const parent = $('fieldsGroup');
    parent.replaceChildren();
    for (const [key, label] of fields) {
      const changed = row[key] !== state.original[index][key];
      const div = make('div', 'field' + (changed ? ' field-edited' : ''));
      const lbl = make('label', '', label + (changed ? ' · изменено' : ''));
      lbl.htmlFor = 'edit_' + key;
      const input = make('textarea');
      input.id = 'edit_' + key;
      input.name = key;
      input.rows = 2;
      input.value = row[key] ?? '';
      // Textareas normalize CRLF visually; a no-op save must not alter API data.
      input.dataset.initialValue = input.value;
      input.disabled = row[key] === null;
      const toggleLabel = make('label', 'null-toggle');
      const toggle = make('input');
      toggle.id = 'null_' + key;
      toggle.type = 'checkbox';
      toggle.checked = row[key] === null;
      toggle.addEventListener('change', () => { input.disabled = toggle.checked; });
      toggleLabel.append(toggle, make('span', '', 'null · не определено'));
      const original = make('small', 'original-value', 'Парсер: ' + displayValue(state.original[index][key]));
      original.id = 'original_' + key;
      input.setAttribute('aria-describedby', original.id);
      div.append(lbl, input, toggleLabel, original);
      parent.append(div);
    }
    $('backdrop').hidden = false;
    $('drawer').inert = false;
    $('drawer').classList.add('open');
    $('drawer').setAttribute('aria-hidden', 'false');
    document.querySelector('.shell').inert = true;
    document.body.classList.add('drawer-open');
    $('closeDrawer').focus();
  }
  function closeRow(restoreFocus = true) {
    $('backdrop').hidden = true;
    $('drawer').classList.remove('open');
    $('drawer').setAttribute('aria-hidden', 'true');
    $('drawer').inert = true;
    document.querySelector('.shell').inert = false;
    document.body.classList.remove('drawer-open');
    const index = state.activeIndex;
    state.activeIndex = null;
    if (restoreFocus && index !== null) {
      const target = state.returnFocus?.isConnected ? state.returnFocus :
        document.querySelector(`tr[data-index="${index}"] .edit-row`) || $('searchInput');
      target.focus();
    }
  }
  $('closeDrawer').addEventListener('click', () => closeRow());
  $('backdrop').addEventListener('click', () => closeRow());
  document.addEventListener('keydown', event => {
    if (state.activeIndex === null) return;
    if (event.key === 'Escape') { event.preventDefault(); closeRow(); }
    if (event.key === 'Tab') {
      const controls = [...$('drawer').querySelectorAll('button, input:not(:disabled), textarea:not(:disabled)')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  $('editForm').addEventListener('submit', event => {
    event.preventDefault();
    const index = state.activeIndex;
    if (index === null) return;
    const next = {...state.results[index]};
    for (const [key, label] of fields) {
      const input = $('edit_' + key), value = input.value;
      if (!$('null_' + key).checked && (value.trim() === '' || value.trim().toLowerCase() === 'null')) {
        $('editError').textContent = `${label}: введите непустую строку или отметьте null.`;
        input.focus();
        return;
      }
      next[key] = $('null_' + key).checked ? null :
        (value === input.dataset.initialValue && next[key] !== null ? next[key] : value);
    }
    state.results[index] = next;
    if (fields.some(([key]) => next[key] !== state.original[index][key])) state.edits.add(index);
    else state.edits.delete(index);
    render();
    closeRow();
    notice('Правки сохранены в текущей вкладке. Confidence остался исходным. Сохраните JSON/CSV.', true);
  });
  $('resetRow').addEventListener('click', () => {
    const index = state.activeIndex;
    if (index === null) return;
    state.results[index] = structuredClone(state.original[index]);
    state.edits.delete(index);
    render();
    closeRow();
    notice('Reset: восстановлен исходный результат парсера, включая confidence.', true);
  });
  updateFilters();
  requestJSON('/health').then(() => {
    $('backendStatus').className = 'status-label ok';
    $('backendStatus').lastChild.textContent = ' API доступен · офлайн-движок';
  }).catch(() => {
    $('backendStatus').className = 'status-label bad';
    $('backendStatus').lastChild.textContent = ' API недоступен';
  });
})();
