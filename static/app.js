(() => {
  const API_BASE = String(window.HUB_API_BASE || '').replace(/\/$/, '');
  const apiUrl = (path) => /^https?:\/\//i.test(path) ? path : `${API_BASE}${path}`;
  const $ = (id) => document.getElementById(id);
  const form = $('scanForm');
  const scanButton = $('scanButton');
  const candidatesNode = $('candidateList');
  const scanStatus = $('scanStatus');
  const previewStatus = $('previewStatus');
  const videoFrame = $('previewFrame');
  const loopControls = $('loopControls');
  const startRange = $('startRange');
  const endRange = $('endRange');
  let activeGame = 'genshin';
  let activeRegion = 'global';
  let scanResult = null;
  let captured = null;
  let loop = null;
  let previewVideo = null;

  function setStatus(node, message, error = false) {
    node.textContent = message;
    node.classList.toggle('error', error);
  }

  async function request(path, options = {}) {
    const response = await fetch(apiUrl(path), { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    return response;
  }

  function resetAnalysis() {
    captured = null; loop = null; previewVideo = null;
    $('previewWorkspace').classList.add('hidden');
    loopControls.classList.add('hidden');
    $('analysisBadge').textContent = 'ยังไม่มีคลิป';
    $('similarityLabel').textContent = '—';
    videoFrame.innerHTML = '<div class="empty-state"><span class="play-icon">▶</span><b>ตัวอย่างพื้นหลัง</b><small>เลือกสื่อจากรายการแล้วสั่งวิเคราะห์</small></div>';
  }

  function renderCandidates() {
    candidatesNode.replaceChildren();
    if (!scanResult?.candidates?.length) return;
    for (const item of scanResult.candidates) {
      const card = document.createElement('article'); card.className = 'candidate';
      const info = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = item.label || item.kind;
      const detail = document.createElement('small');
      detail.textContent = [item.width && item.height ? `${item.width}×${item.height}` : '', item.duration ? `${Number(item.duration).toFixed(2)}s` : '', item.selector || '', item.kind].filter(Boolean).join(' · ');
      info.append(title, detail);
      const button = document.createElement('button'); button.className = 'secondary'; button.type = 'button'; button.textContent = item.kind === 'image' ? 'ดูภาพนิ่ง' : item.kind === 'element' ? 'จับฉากประกอบ' : 'จับและวิเคราะห์';
      button.addEventListener('click', () => captureCandidate(item, button));
      card.append(info, button); candidatesNode.append(card);
    }
  }

  function setMode(mode) {
    const catalog = mode === 'catalog';
    $('catalogWorkspace').classList.toggle('hidden', !catalog);
    $('customWorkspace').classList.toggle('hidden', catalog);
    for (const button of document.querySelectorAll('.mode-tab')) button.classList.toggle('active', button.dataset.mode === mode);
  }

  async function loadCatalog() {
    const status = $('catalogStatus'); const grid = $('catalogGrid');
    grid.replaceChildren(); setStatus(status, 'กำลังโหลดคลังวิดีโอ…');
    try {
      const response = await request(`/api/catalog?game=${encodeURIComponent(activeGame)}&region=${encodeURIComponent(activeRegion)}`);
      const result = await response.json();
      setStatus(status, `พบวิดีโอ ${result.count} รายการ · ${activeRegion === 'global' ? 'Global' : 'China'}`);
      if (!result.items.length) { setStatus(status, 'ยังไม่พบวิดีโอของเกมนี้'); return; }
      for (const item of result.items) {
        const card = document.createElement('article'); card.className = 'catalog-card';
        const cover = document.createElement('img'); cover.className = 'catalog-cover'; cover.src = item.coverUrl; cover.alt = ''; cover.loading = 'lazy';
        const info = document.createElement('div'); info.className = 'catalog-card-info';
        const title = document.createElement('strong'); title.textContent = item.title;
        const metadata = document.createElement('small');
        const size = item.size ? `${(item.size / (1024 * 1024)).toFixed(1)} MB` : '';
        metadata.textContent = [item.width && item.height ? `${item.width}×${item.height}` : '', item.duration ? `${Number(item.duration).toFixed(2)}s` : '', item.framerate ? `${item.framerate} fps` : '', size].filter(Boolean).join(' · ');
        const button = document.createElement('button'); button.className = 'primary'; button.type = 'button'; button.textContent = 'ดาวน์โหลดเข้าโปรเจกต์';
        button.addEventListener('click', () => importCatalogItem(item, button));
        info.append(title, metadata, button); card.append(cover, info); grid.append(card);
      }
    } catch (error) { setStatus(status, error.message || String(error), true); }
  }

  async function importCatalogItem(item, button) {
    button.disabled = true; setMode('catalog'); resetAnalysis();
    $('previewWorkspace').classList.remove('hidden');
    setStatus(previewStatus, 'กำลังดาวน์โหลดวิดีโอต้นฉบับเข้าโปรเจกต์…');
    scanResult = { url: item.url, pageTitle: item.title };
    try {
      const response = await request('/api/catalog/import', { method: 'POST', body: JSON.stringify({ url: item.url, title: item.title, coverUrl: item.coverUrl }) });
      captured = await response.json();
      setStatus(previewStatus, `ดาวน์โหลดแล้ว (${captured.fileName}) · กำลังวิเคราะห์จุดวน…`);
      const analysisResponse = await request('/api/analyze', { method: 'POST', body: JSON.stringify({ mediaId: captured.mediaId }) });
      loop = await analysisResponse.json(); configureLoop(captured, loop);
      setStatus(previewStatus, 'วิดีโออยู่ใน data ของโปรเจกต์แล้ว · ตรวจตัวอย่างและปรับช่วง Loop ได้');
    } catch (error) { setStatus(previewStatus, error.message || String(error), true); }
    finally { button.disabled = false; }
  }

  for (const button of document.querySelectorAll('.mode-tab')) button.addEventListener('click', () => setMode(button.dataset.mode));
  for (const button of document.querySelectorAll('.game-tab')) button.addEventListener('click', () => {
    activeGame = button.dataset.game;
    for (const tab of document.querySelectorAll('.game-tab')) tab.classList.toggle('active', tab === button);
    loadCatalog();
  });
  $('catalogRegion').addEventListener('change', (event) => { activeRegion = event.target.value; loadCatalog(); });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    resetAnalysis(); candidatesNode.replaceChildren(); scanButton.disabled = true;
    setStatus(scanStatus, 'กำลังเปิดหน้าเว็บและค้นหาสื่อ…');
    try {
      const response = await request('/api/scan', { method: 'POST', body: JSON.stringify({
        url: $('pageUrl').value.trim(), selector: $('sceneSelector').value.trim(),
        captureSeconds: Number($('captureSeconds').value)
      }) });
      scanResult = await response.json(); renderCandidates();
      const count = scanResult.candidates.length;
      setStatus(scanStatus, count ? `พบ ${count} แหล่งสื่อใน “${scanResult.pageTitle || 'หน้าเว็บ'}”` : 'เปิดหน้าได้ แต่ยังไม่พบวิดีโอ, Canvas หรือ Texture ที่รองรับ');
    } catch (error) {
      setStatus(scanStatus, error.message || String(error), true);
    } finally { scanButton.disabled = false; }
  });

  async function captureCandidate(candidate, button) {
    for (const node of candidatesNode.querySelectorAll('button')) node.disabled = true;
    resetAnalysis(); $('previewWorkspace').classList.remove('hidden'); setStatus(previewStatus, 'กำลังดึงสื่อจากหน้าเว็บ…');
    try {
      const captureResponse = await request('/api/capture', { method: 'POST', body: JSON.stringify({ scanId: scanResult.scanId, candidateId: candidate.id }) });
      captured = await captureResponse.json();
      if (candidate.kind === 'image') {
        showPreview(captured.mediaUrl, 'image');
        setStatus(previewStatus, 'ได้ภาพพื้นหลังแล้ว · ภาพนิ่งไม่มีจุดวนแอนิเมชันให้วิเคราะห์');
        return;
      }
      setStatus(previewStatus, `กำลังวิเคราะห์ภาพต้นและท้ายของคลิป ${Number(captured.duration).toFixed(2)} วินาที…`);
      const analysisResponse = await request('/api/analyze', { method: 'POST', body: JSON.stringify({ mediaId: captured.mediaId }) });
      loop = await analysisResponse.json();
      configureLoop(captured, loop);
      setStatus(previewStatus, 'พบช่วงภาพที่ต่อกันได้ · เล่นตัวอย่างเพื่อเช็กจังหวะก่อนบันทึก');
    } catch (error) {
      setStatus(previewStatus, error.message || String(error), true);
    } finally { for (const node of candidatesNode.querySelectorAll('button')) node.disabled = false; }
  }

  function showPreview(mediaUrl, kind = 'video') {
    videoFrame.replaceChildren();
    if (kind === 'image') {
      const image = document.createElement('img'); image.src = apiUrl(mediaUrl); image.alt = 'Live wallpaper preview'; videoFrame.append(image); return;
    }
    const video = document.createElement('video');
    video.src = apiUrl(mediaUrl); video.controls = true; video.muted = true; video.playsInline = true; video.preload = 'auto';
    video.addEventListener('timeupdate', () => {
      if (!loop) return;
      const start = Number(startRange.value); const end = Number(endRange.value);
      if (end > start && video.currentTime >= end) video.currentTime = start;
    });
    video.addEventListener('loadedmetadata', () => { if (loop) video.currentTime = Number(startRange.value); });
    videoFrame.append(video); previewVideo = video;
    video.play().catch(() => {});
  }

  function updateLoopLabels() {
    $('startLabel').textContent = `${Number(startRange.value).toFixed(2)}s`;
    $('endLabel').textContent = `${Number(endRange.value).toFixed(2)}s`;
    if (previewVideo && Number(endRange.value) <= Number(startRange.value)) endRange.value = Math.min(Number(startRange.max), Number(startRange.value) + 0.1);
  }

  function configureLoop(media, result) {
    const duration = Number(result.duration || media.duration || 0);
    startRange.min = '0'; startRange.max = String(duration); startRange.value = String(result.start);
    endRange.min = '0'; endRange.max = String(duration); endRange.value = String(result.end);
    $('similarityLabel').textContent = `${Number(result.similarity).toFixed(1)}%`;
    $('analysisBadge').textContent = `คลิป ${duration.toFixed(2)}s`;
    $('previewTitle').textContent = scanResult?.pageTitle || 'ตรวจจุดวนภาพ';
    $('previewWorkspace').classList.remove('hidden');
    loopControls.classList.remove('hidden');
    startRange.oninput = updateLoopLabels; endRange.oninput = updateLoopLabels;
    updateLoopLabels(); showPreview(media.mediaUrl);
  }

  $('saveToHub').addEventListener('click', async () => {
    if (!captured) return;
    try {
      const item = { title: scanResult.pageTitle || 'Live Wallpaper', sourceUrl: scanResult.url, mediaId: captured.mediaId,
        start: Number(startRange.value), end: Number(endRange.value), similarity: Number(loop?.similarity || 100) };
      await request('/api/library', { method: 'POST', body: JSON.stringify(item) });
      setStatus(previewStatus, 'เพิ่มลงคลังเรียบร้อย'); await loadLibrary();
    } catch (error) { setStatus(previewStatus, error.message || String(error), true); }
  });

  $('exportProject').addEventListener('click', async () => {
    if (!captured) return;
    try {
      const response = await request('/api/export', { method: 'POST', body: JSON.stringify({
        title: scanResult.pageTitle || 'Live Wallpaper', mediaId: captured.mediaId,
        start: Number(startRange.value), end: Number(endRange.value)
      }) });
      const blob = await response.blob(); const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = 'LiveWallpaper-Lively.zip'; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1500);
      setStatus(previewStatus, 'ดาวน์โหลดโปรเจกต์ Lively ZIP แล้ว');
    } catch (error) { setStatus(previewStatus, error.message || String(error), true); }
  });

  async function loadLibrary() {
    try {
      const response = await request('/api/library'); const entries = await response.json();
      const grid = $('libraryGrid'); grid.replaceChildren();
      if (!entries.length) { const empty = document.createElement('div'); empty.className = 'library-empty'; empty.textContent = 'ยังไม่มีรายการในคลัง'; grid.append(empty); return; }
      for (const entry of entries) {
        const card = document.createElement('article'); card.className = 'library-card';
        const video = document.createElement('video'); video.src = apiUrl(entry.mediaUrl); video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'metadata';
        video.addEventListener('timeupdate', () => { if (video.currentTime >= entry.end) video.currentTime = entry.start; });
        video.addEventListener('loadedmetadata', () => { video.currentTime = entry.start; video.play().catch(() => {}); });
        const info = document.createElement('div'); info.className = 'library-info';
        const title = document.createElement('strong'); title.textContent = entry.title;
        const detail = document.createElement('small'); detail.textContent = `Loop ${Number(entry.start).toFixed(2)}–${Number(entry.end).toFixed(2)}s · ภาพคล้าย ${Number(entry.similarity).toFixed(1)}%`;
        const actions = document.createElement('div'); actions.className = 'card-actions';
        const exportButton = document.createElement('button'); exportButton.className = 'secondary'; exportButton.textContent = 'ส่งออก ZIP';
        exportButton.addEventListener('click', async () => {
          try {
            const result = await request('/api/export', { method: 'POST', body: JSON.stringify({ title: entry.title, mediaId: entry.mediaId, start: entry.start, end: entry.end }) });
            const blob = await result.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'LiveWallpaper-Lively.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1500);
          } catch (error) { alert(error.message || String(error)); }
        });
        const removeButton = document.createElement('button'); removeButton.className = 'secondary'; removeButton.textContent = 'ลบ';
        removeButton.addEventListener('click', async () => { await request(`/api/library/${encodeURIComponent(entry.id)}`, { method: 'DELETE' }); await loadLibrary(); });
        actions.append(exportButton, removeButton); info.append(title, detail, actions); card.append(video, info); grid.append(card);
      }
    } catch (error) { $('libraryGrid').innerHTML = '<div class="library-empty">ยังเชื่อมต่อคลังไม่ได้ · เปิด backend ก่อน</div>'; }
  }

  $('refreshLibrary').addEventListener('click', loadLibrary);

  async function connect() {
    try {
      const response = await request('/api/health'); const data = await response.json();
      $('connectionState').textContent = data.library === 'persistent' ? 'เชื่อมต่อคลังแล้ว' : 'เชื่อมต่อ backend แล้ว';
      document.querySelector('.connection').classList.add('online');
    } catch (_) { $('connectionState').textContent = 'ยังไม่เชื่อมต่อ backend'; }
    await loadLibrary();
  }
  loadCatalog();
  connect();
})();
