const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { createRequire } = require("node:module");
const { createServer } = require("node:http");
const { once } = require("node:events");
const path = require("node:path");
const vm = require("node:vm");

const dependencies = createRequire(path.resolve(process.argv[2], "package.json"));
const { Server } = dependencies("socket.io");
const { io: connect } = dependencies("socket.io-client");
const source = readFileSync(process.argv[3], "utf8");

function middleware(fixture) {
	const module = { exports: {} };
	const timers = new Set();
	const sandbox = {
		module,
		URLSearchParams,
		AbortSignal,
		setTimeout: (callback) => { timers.add(callback); return callback; },
		clearTimeout: (callback) => timers.delete(callback),
		setInterval: (callback) => { fixture.recheck = callback; timers.add(callback); return callback; },
		clearInterval: (callback) => timers.delete(callback),
		fetch: async () => {
			if (fixture.fetchTime != null) fixture.now = fixture.fetchTime;
			return { ok: fixture.ok, json: async () => ({ message: fixture.message }) };
		},
		require: (name) => {
			if (name === "node:perf_hooks") return { performance: { now: () => fixture.now } };
			if (name === "../../node_utils") return {
				get_conf: () => ({}),
				get_redis_subscriber: () => ({ isOpen: true, get: async () => null }),
			};
			if (name === "../utils") return { get_url: () => "http://unused.invalid" };
			return dependencies(name);
		},
	};
	vm.runInNewContext(source, sandbox, { filename: process.argv[3] });
	fixture.timers = timers;
	return module.exports;
}

async function run(channel, transport, expire, native = false) {
	const fixture = {
		now: 0, ok: true,
		message: { user: "fixture", user_type: "Website User", installed_apps: [], passport_ttl: native ? null : 1 },
	};
	const server = new Server(createServer(), { serveClient: false });
	const namespace = server.of("/test.local");
	namespace.use(middleware(fixture));
	let handled = 0;
	let received = false;
	let client;
	let serverSocket;
	try {
		namespace.on("connection", socket => {
			serverSocket = socket;
			socket.join("application");
			if (channel === "initial-output") {
				if (expire) fixture.now = 2000;
				socket.emit("protected", "initial");
			}
			socket.on("exercise", acknowledge => {
				handled += 1;
				if (expire) fixture.now = 2000;
				if (channel === "direct" || channel === "inbound") socket.emit("protected", "payload");
				if (channel === "room") namespace.to("application").emit("protected", "payload");
				if (channel === "namespace") namespace.emit("protected", "payload");
				if (channel === "binary") namespace.to("application").emit("protected", Buffer.from("payload"));
				if (channel === "ack") acknowledge("payload");
				if (channel === "binary-ack") acknowledge(Buffer.from("payload"));
			});
		});
		server.httpServer.listen(0, "127.0.0.1");
		await once(server.httpServer, "listening");
		client = connect(`http://127.0.0.1:${server.httpServer.address().port}/test.local`, {
			transports: [transport], reconnection: false, forceNew: true, autoConnect: false,
			extraHeaders: {
				Origin: `http://127.0.0.1:${server.httpServer.address().port}`,
				"X-Frappe-Site-Name": "test.local", Cookie: "sid=fixture",
			},
		});
		client.on("protected", () => { received = true; });
		const connected = once(client, "connect");
		client.connect();
		await Promise.race([connected, once(client, "connect_error").then(([error]) => { throw error; })]);
		if (channel === "inbound" && expire) fixture.now = 2000;
		if (channel === "renewal") {
			fixture.now = 500;
			fixture.fetchTime = expire ? 2000 : 500;
			await fixture.recheck();
			fixture.now = expire ? 2000 : 1200;
			serverSocket.emit("protected", "after-renewal");
		} else if (channel !== "initial-output") {
			client.emit("exercise", () => { received = true; });
		}
		await new Promise(resolve => setTimeout(resolve, 75));
		assert.equal(received, native || !expire, `${channel}/${transport}/expired=${expire}/native=${native}`);
		if (channel === "inbound" && expire && !native) assert.equal(handled, 0);
	} finally {
		client?.disconnect();
		await new Promise(resolve => server.close(resolve));
		assert.equal(fixture.timers.size, 0, "disconnect must clean both timers");
	}
}

async function rejectsInvalidDeadline(ttl, fetchTime) {
	const fixture = { now: 0, fetchTime, ok: true, message: { user: "fixture", installed_apps: [], passport_ttl: ttl } };
	const authenticate = middleware(fixture);
	const socket = { nsp: { name: "/test.local" }, request: { headers: {
		host: "test.local", origin: "http://test.local", cookie: "sid=fixture",
	} } };
	const error = await new Promise(resolve => authenticate(socket, resolve));
	assert.ok(error, "invalid/expired initial deadline must reject authentication");
	assert.equal(fixture.timers.size, 0);
}

async function main() {
	let cases = 0;
	for (const transport of ["websocket", "polling"]) {
		for (const channel of ["direct", "room", "namespace", "binary", "ack", "binary-ack", "initial-output", "inbound", "renewal"]) {
			for (const expire of [false, true]) {
				await run(channel, transport, expire);
				cases += 1;
			}
		}
		await run("room", transport, true, true);
		cases += 1;
	}
	for (const ttl of [0, -1, NaN, Infinity, "1"]) {
		await rejectsInvalidDeadline(ttl, 0);
		cases += 1;
	}
	await rejectsInvalidDeadline(1, 2000);
	cases += 1;
	console.log(JSON.stringify({ cases, result: "passed", scope: "candidate middleware; real transports; controlled clock and authentication response" }));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
