'use strict';

const path = require('path');
const express = require('express');
const http = require('http');
const { WebSocketServer } = require('ws');
const { GeneratorEngine } = require('./lib/generatorEngine');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const engine = new GeneratorEngine();

// --- REST API ---------------------------------------------------------

app.post('/api/test-connection', async (req, res) => {
  try {
    const result = await engine.testConnection(req.body || {});
    res.json(result);
  } catch (err) {
    res.status(400).json({ ok: false, message: err.message });
  }
});

app.post('/api/generator/start', async (req, res) => {
  try {
    const body = req.body || {};
    const config = {
      connectionString: body.connectionString,
      username: body.username,
      password: body.password,
      useTLS: !!body.useTLS,
      bucket: body.bucket,
      scope: body.scope || '',
      collection: body.collection || '',
      rateMBps: Number(body.rateMBps) > 0 ? Number(body.rateMBps) : 1,
    };
    const snap = await engine.start(config);
    res.json({ ok: true, snapshot: snap });
  } catch (err) {
    res.status(400).json({ ok: false, message: err.message });
  }
});

app.post('/api/generator/stop', async (req, res) => {
  const snap = await engine.stop();
  res.json({ ok: true, snapshot: snap });
});

app.get('/api/generator/status', (req, res) => {
  res.json(engine.getSnapshot());
});

// --- Server + WebSocket for live stats ---------------------------------

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

function broadcast(snapshot) {
  const payload = JSON.stringify({ type: 'stats', data: snapshot });
  wss.clients.forEach((client) => {
    if (client.readyState === client.OPEN) client.send(payload);
  });
}

engine.on('update', broadcast);

wss.on('connection', (ws) => {
  ws.send(JSON.stringify({ type: 'stats', data: engine.getSnapshot() }));
});

const PORT = process.env.PORT || 4300;
server.listen(PORT, () => {
  console.log(`Couchbase Data Generator running at http://localhost:${PORT}`);
});

process.on('SIGINT', async () => {
  await engine.stop();
  process.exit(0);
});
