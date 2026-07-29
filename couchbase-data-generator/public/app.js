(() => {
  'use strict';

  // ---------------------------------------------------------------
  // View / nav switching
  // ---------------------------------------------------------------
  const views = { dashboard: document.getElementById('dashboardView'), wizard: document.getElementById('wizardView') };
  const navItems = document.querySelectorAll('.nav-item');

  function showView(name) {
    Object.entries(views).forEach(([key, el]) => el.classList.toggle('hidden', key !== name));
    navItems.forEach((btn) => btn.classList.toggle('active', btn.dataset.view === name));
  }
  navItems.forEach((btn) => btn.addEventListener('click', () => showView(btn.dataset.view)));

  // ---------------------------------------------------------------
  // Wizard step navigation
  // ---------------------------------------------------------------
  const stepEls = document.querySelectorAll('.step');
  const stepPanels = document.querySelectorAll('.step-panel');

  function goToStep(n) {
    stepEls.forEach((el) => el.classList.toggle('active', Number(el.dataset.step) === n));
    stepPanels.forEach((el) => el.classList.toggle('hidden', Number(el.dataset.stepPanel) !== n));
    if (n === 3) populateReview();
  }
  document.querySelectorAll('[data-next]').forEach((btn) =>
    btn.addEventListener('click', () => goToStep(Number(btn.dataset.next)))
  );
  document.querySelectorAll('[data-prev]').forEach((btn) =>
    btn.addEventListener('click', () => goToStep(Number(btn.dataset.prev)))
  );

  function readConfig() {
    return {
      connectionString: document.getElementById('cfgConnStr').value.trim(),
      username: document.getElementById('cfgUsername').value,
      password: document.getElementById('cfgPassword').value,
      useTLS: document.getElementById('cfgTLS').checked,
      bucket: document.getElementById('cfgBucket').value.trim(),
      scope: document.getElementById('cfgScope').value.trim(),
      collection: document.getElementById('cfgCollection').value.trim(),
      rateMBps: Number(document.getElementById('cfgRate').value) || 1,
    };
  }

  function populateReview() {
    const c = readConfig();
    document.getElementById('revConnStr').textContent = c.connectionString || '—';
    document.getElementById('revUsername').textContent = c.username || '—';
    document.getElementById('revTLS').textContent = c.useTLS ? 'Enabled' : 'Disabled';
    document.getElementById('revBucket').textContent = c.bucket || '—';
    document.getElementById('revScopeColl').textContent = `${c.scope || '_default'} / ${c.collection || '_default'}`;
    document.getElementById('revRate').textContent = `${c.rateMBps} MB/s`;
  }

  // ---------------------------------------------------------------
  // Test connection
  // ---------------------------------------------------------------
  document.getElementById('testConnBtn').addEventListener('click', async () => {
    const resultEl = document.getElementById('testConnResult');
    resultEl.textContent = 'Testing…';
    resultEl.className = 'test-result';
    const c = readConfig();
    try {
      const res = await fetch('/api/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(c),
      });
      const data = await res.json();
      if (data.ok) {
        const nodeCount = data.nodes ? data.nodes.length : 0;
        resultEl.textContent = `Connected — ${nodeCount} node(s) discovered${data.bucketInfo ? `, bucket "${data.bucketInfo.name}" reachable` : ''}.`;
        resultEl.className = 'test-result ok';
      } else {
        throw new Error(data.message || 'Connection failed');
      }
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = 'test-result fail';
    }
  });

  // ---------------------------------------------------------------
  // Start generator from wizard
  // ---------------------------------------------------------------
  document.getElementById('wizardStartBtn').addEventListener('click', async () => {
    const errEl = document.getElementById('wizardError');
    errEl.classList.add('hidden');
    const c = readConfig();
    if (!c.connectionString || !c.bucket) {
      errEl.textContent = 'Connection string and bucket name are required.';
      errEl.classList.remove('hidden');
      return;
    }
    try {
      const res = await fetch('/api/generator/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(c),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.message || 'Failed to start generator');
      showView('dashboard');
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });

  // ---------------------------------------------------------------
  // Start / Stop button on dashboard
  // ---------------------------------------------------------------
  const startStopBtn = document.getElementById('startStopBtn');
  let lastConfig = null;

  startStopBtn.addEventListener('click', async () => {
    startStopBtn.disabled = true;
    if (startStopBtn.dataset.mode === 'stop') {
      await fetch('/api/generator/stop', { method: 'POST' });
    } else {
      const c = lastConfig || readConfig();
      await fetch('/api/generator/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(c),
      });
    }
  });

  // ---------------------------------------------------------------
  // Throughput sparkline (canvas, no external deps)
  // ---------------------------------------------------------------
  const canvas = document.getElementById('throughputChart');
  const ctx = canvas.getContext('2d');

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * devicePixelRatio;
    canvas.height = 140 * devicePixelRatio;
    ctx.scale(devicePixelRatio, devicePixelRatio);
  }
  window.addEventListener('resize', () => { resizeCanvas(); drawChart(lastHistory); });

  let lastHistory = [];
  function drawChart(history) {
    lastHistory = history || [];
    const w = canvas.getBoundingClientRect().width;
    const h = 140;
    ctx.clearRect(0, 0, w, h);

    const max = Math.max(1, ...lastHistory.map((p) => p.mbps));
    const padding = 24;
    const plotW = w - padding * 2;
    const plotH = h - padding * 2;

    // grid lines
    ctx.strokeStyle = 'rgba(139,147,161,0.15)';
    ctx.lineWidth = 1;
    ctx.font = '10px -apple-system, sans-serif';
    ctx.fillStyle = '#8b93a1';
    for (let i = 0; i <= 4; i++) {
      const y = padding + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(w - padding, y);
      ctx.stroke();
      const val = (max * (4 - i)) / 4;
      ctx.fillText(val.toFixed(1), 2, y + 3);
    }

    if (lastHistory.length < 2) return;

    ctx.strokeStyle = '#33c7d6';
    ctx.lineWidth = 2;
    ctx.beginPath();
    lastHistory.forEach((p, i) => {
      const x = padding + (plotW * i) / (lastHistory.length - 1);
      const y = padding + plotH - (p.mbps / max) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // fill under curve
    ctx.lineTo(padding + plotW, padding + plotH);
    ctx.lineTo(padding, padding + plotH);
    ctx.closePath();
    ctx.fillStyle = 'rgba(51,199,214,0.08)';
    ctx.fill();

    // dot at latest point
    const last = lastHistory[lastHistory.length - 1];
    const lx = padding + plotW;
    const ly = padding + plotH - (last.mbps / max) * plotH;
    ctx.fillStyle = '#33c7d6';
    ctx.beginPath();
    ctx.arc(lx, ly, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
  resizeCanvas();
  drawChart([]);

  // ---------------------------------------------------------------
  // Flow diagram + stat rendering
  // ---------------------------------------------------------------
  const badgeEl = document.getElementById('statusBadge');
  const pulseLeft = document.getElementById('pulseLeft');
  const pulseRight = document.getElementById('pulseRight');
  const flowSourceDot = document.querySelector('#flowSvg g:nth-of-type(1) .node-dot-idle, #flowSvg g:nth-of-type(1) .node-dot-active');

  function fmtInt(n) { return Number(n || 0).toLocaleString(); }

  function render(snapshot) {
    if (!snapshot) return;

    // status badge
    badgeEl.textContent = snapshot.status.toUpperCase();
    badgeEl.className = `badge badge-${snapshot.status}`;

    // start/stop button
    const running = snapshot.status === 'running' || snapshot.status === 'connecting';
    startStopBtn.disabled = snapshot.status === 'connecting' || snapshot.status === 'stopping';
    startStopBtn.textContent = running ? 'Stop Generator' : 'Start Generator';
    startStopBtn.dataset.mode = running ? 'stop' : 'start';
    startStopBtn.classList.toggle('stop', running);

    // stat cards
    document.getElementById('statDocs').textContent = fmtInt(snapshot.docsGenerated);
    document.getElementById('statThroughput').textContent = `${snapshot.throughputMBs.toFixed(2)} MB/s`;
    document.getElementById('statDocsPerSec').textContent = fmtInt(snapshot.docsPerSec);
    document.getElementById('statErrorRate').textContent = `${snapshot.errorRate.toFixed(2)}%`;
    document.getElementById('statElapsed').textContent = `${Math.round(snapshot.elapsedSec)}s`;
    if (snapshot.config) {
      document.getElementById('statTarget').textContent = `${Number(snapshot.config.rateMBps).toFixed(2)} MB/s`;
      lastConfig = snapshot.config;
    }

    // flow diagram
    const isRunning = snapshot.status === 'running';
    pulseLeft.classList.toggle('running', isRunning);
    pulseRight.classList.toggle('running', isRunning);
    document.querySelectorAll('.flow-arrow').forEach((el) => el.classList.toggle('active', isRunning));
    document.querySelectorAll('.node-dot').forEach((el) => {
      el.classList.toggle('node-dot-active', isRunning);
      el.classList.toggle('node-dot-idle', !isRunning);
    });
    document.getElementById('flowGenLabel').textContent = isRunning ? 'generating' : snapshot.status;
    document.getElementById('flowConnLabel').textContent = isRunning || snapshot.status === 'connecting'
      ? 'connected'
      : 'not connected';
    document.getElementById('flowDocLabel').textContent = `${fmtInt(snapshot.docsGenerated)} docs generated`;
    document.getElementById('flowBucketLabel').textContent = `bucket: ${snapshot.config ? snapshot.config.bucket : '—'}`;
    document.getElementById('throughputLabel').textContent = `${snapshot.throughputMBs.toFixed(2)} MB/s`;

    // error panel
    const errorPanel = document.getElementById('errorPanel');
    if (snapshot.lastError) {
      document.getElementById('errorText').textContent = snapshot.lastError;
      errorPanel.classList.remove('hidden');
    } else {
      errorPanel.classList.add('hidden');
    }

    // chart
    if (snapshot.history) drawChart(snapshot.history);
  }

  // ---------------------------------------------------------------
  // WebSocket live updates (with polling fallback)
  // ---------------------------------------------------------------
  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'stats') render(msg.data);
      } catch (_) { /* ignore malformed frame */ }
    };
    ws.onclose = () => setTimeout(connectWS, 1500);
    ws.onerror = () => ws.close();
  }

  async function pollOnce() {
    try {
      const res = await fetch('/api/generator/status');
      render(await res.json());
    } catch (_) { /* server may not be up yet */ }
  }

  pollOnce();
  connectWS();
  goToStep(1);
  showView('dashboard');
})();
