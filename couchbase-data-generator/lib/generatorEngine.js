'use strict';

const { EventEmitter } = require('events');
const { generateCustomerProfile } = require('./customer360');

let couchbase = null;
let couchbaseLoadError = null;
try {
  // Loaded lazily so the app still boots (and the UI still works) even in
  // environments where the native Couchbase SDK bindings aren't installed.
  couchbase = require('couchbase');
} catch (err) {
  couchbaseLoadError = err;
}

const TICK_MS = 250; // stats/batch cadence
const HISTORY_LENGTH = 60; // ~15s of ticks kept for the sparkline

function bytesOf(obj) {
  return Buffer.byteLength(JSON.stringify(obj), 'utf8');
}

class GeneratorEngine extends EventEmitter {
  constructor() {
    super();
    this.reset();
  }

  reset() {
    this.status = 'idle'; // idle | connecting | running | stopping | error
    this.config = null;
    this.cluster = null;
    this.collection = null;
    this.timer = null;
    this.startedAt = null;
    this.docsGenerated = 0;
    this.bytesGenerated = 0;
    this.errors = 0;
    this.attempts = 0;
    this.lastError = null;
    this.history = [];
    this.avgDocBytes = 1800; // seed estimate, refined as docs are generated
  }

  getSnapshot() {
    const elapsedSec = this.startedAt ? (Date.now() - this.startedAt) / 1000 : 0;
    const throughputBps = elapsedSec > 0 ? this.bytesGenerated / elapsedSec : 0;
    const docsPerSec = elapsedSec > 0 ? this.docsGenerated / elapsedSec : 0;
    const errorRate = this.attempts > 0 ? this.errors / this.attempts : 0;
    return {
      status: this.status,
      config: this.config
        ? {
            connectionString: this.config.connectionString,
            bucket: this.config.bucket,
            scope: this.config.scope,
            collection: this.config.collection,
            rateMBps: this.config.rateMBps,
            useTLS: !!this.config.useTLS,
          }
        : null,
      docsGenerated: this.docsGenerated,
      bytesGenerated: this.bytesGenerated,
      throughputMBs: Number((throughputBps / (1024 * 1024)).toFixed(3)),
      docsPerSec: Number(docsPerSec.toFixed(1)),
      errorRate: Number((errorRate * 100).toFixed(2)),
      errors: this.errors,
      elapsedSec: Number(elapsedSec.toFixed(1)),
      lastError: this.lastError,
      history: this.history,
    };
  }

  _pushHistory(point) {
    this.history.push(point);
    if (this.history.length > HISTORY_LENGTH) this.history.shift();
  }

  async testConnection({ connectionString, username, password, useTLS, bucket }) {
    if (!couchbase) {
      throw new Error(
        `Couchbase SDK is not available in this environment (${couchbaseLoadError?.message || 'module not installed'}). Run "npm install" on a machine with build tools for the native bindings.`
      );
    }
    const connStr = normalizeConnectionString(connectionString, useTLS);
    const cluster = await couchbase.connect(connStr, {
      username,
      password,
      timeouts: { connectTimeout: 8000, kvTimeout: 5000 },
    });

    const nodes = [];
    try {
      const diag = await cluster.diagnostics();
      for (const svc of Object.values(diag.services || {})) {
        for (const entry of svc) {
          nodes.push({ id: entry.remote || entry.id, state: entry.state });
        }
      }
    } catch (_) {
      /* diagnostics best-effort only */
    }

    let bucketInfo = null;
    if (bucket) {
      const b = cluster.bucket(bucket);
      await b.ping();
      bucketInfo = { name: bucket, reachable: true };
    }

    await cluster.close();
    return { ok: true, nodes, bucketInfo };
  }

  async start(config) {
    if (this.status === 'running' || this.status === 'connecting') {
      throw new Error('Generator is already running.');
    }
    if (!couchbase) {
      throw new Error(
        `Couchbase SDK is not available in this environment (${couchbaseLoadError?.message || 'module not installed'}). Run "npm install" on a machine with build tools for the native bindings.`
      );
    }

    this.reset();
    this.status = 'connecting';
    this.config = config;
    this.emit('update', this.getSnapshot());

    try {
      const connStr = normalizeConnectionString(config.connectionString, config.useTLS);
      this.cluster = await couchbase.connect(connStr, {
        username: config.username,
        password: config.password,
        timeouts: { connectTimeout: 8000, kvTimeout: 5000 },
      });
      const bucket = this.cluster.bucket(config.bucket);
      const scope = config.scope ? bucket.scope(config.scope) : bucket.defaultScope();
      this.collection = config.collection
        ? scope.collection(config.collection)
        : bucket.defaultCollection();
    } catch (err) {
      this.status = 'error';
      this.lastError = err.message;
      this.emit('update', this.getSnapshot());
      throw err;
    }

    this.status = 'running';
    this.startedAt = Date.now();
    this.emit('update', this.getSnapshot());
    this._scheduleTick();
    return this.getSnapshot();
  }

  _scheduleTick() {
    this.timer = setTimeout(() => this._tick(), TICK_MS);
  }

  async _tick() {
    if (this.status !== 'running') return;

    const targetBytesPerSec = Math.max(0.01, this.config.rateMBps) * 1024 * 1024;
    const budgetBytes = (targetBytesPerSec * TICK_MS) / 1000;
    const estDocs = Math.max(1, Math.round(budgetBytes / this.avgDocBytes));

    const batch = [];
    for (let i = 0; i < estDocs; i++) {
      const doc = generateCustomerProfile();
      batch.push(doc);
    }

    const inserts = batch.map(async (doc) => {
      this.attempts++;
      try {
        await this.collection.upsert(doc.customerId, doc);
        const size = bytesOf(doc);
        this.docsGenerated++;
        this.bytesGenerated += size;
        // exponential moving average keeps pacing accurate as doc shape varies
        this.avgDocBytes = this.avgDocBytes * 0.9 + size * 0.1;
      } catch (err) {
        this.errors++;
        this.lastError = err.message;
      }
    });

    try {
      await Promise.all(inserts);
    } finally {
      const snap = this.getSnapshot();
      this._pushHistory({ t: Date.now(), mbps: snap.throughputMBs });
      this.emit('update', snap);
      if (this.status === 'running') this._scheduleTick();
    }
  }

  async stop() {
    if (this.status !== 'running' && this.status !== 'connecting') {
      return this.getSnapshot();
    }
    this.status = 'stopping';
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    try {
      if (this.cluster) await this.cluster.close();
    } catch (_) {
      /* ignore close errors */
    }
    this.cluster = null;
    this.collection = null;
    this.status = 'idle';
    this.emit('update', this.getSnapshot());
    return this.getSnapshot();
  }
}

function normalizeConnectionString(input, useTLS) {
  let s = (input || '').trim();
  if (!/^couchbases?:\/\//.test(s)) {
    s = (useTLS ? 'couchbases://' : 'couchbase://') + s;
  }
  if (useTLS && s.startsWith('couchbase://')) {
    s = s.replace('couchbase://', 'couchbases://');
  }
  return s;
}

module.exports = { GeneratorEngine, couchbaseLoadError: () => couchbaseLoadError };
