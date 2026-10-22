const fs = require('node:fs');
const {io} = require('../.bench/apps/frappe/node_modules/socket.io-client');
const {performance} = require('node:perf_hooks');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const started = performance.now();
const socket = io('http://127.0.0.1:9080/site1.localhost', {
  transports: ['websocket'], reconnection: false,
  extraHeaders: {Origin:'http://site1.localhost:8080',Host:'site1.localhost:9080',Cookie:`sid=${input.sid}`}
});
let connected = false;
const timeout = setTimeout(() => {console.error('FAIL socket did not terminate');process.exit(1);}, 16000);
socket.on('connect', () => { connected = true; console.log('PASS realtime connection admitted'); });
socket.on('disconnect', () => {
  clearTimeout(timeout);
  if (!connected || performance.now()-started > 12000) process.exit(1);
  console.log('PASS realtime disconnect at shortened lease deadline');
  process.exit(0);
});
socket.on('connect_error', () => {
  clearTimeout(timeout);
  if (input.reject) { console.log('PASS expired realtime connection rejected');process.exit(0); }
  console.error('FAIL valid realtime connection rejected');process.exit(1);
});
