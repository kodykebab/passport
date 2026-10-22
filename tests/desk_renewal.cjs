// Execute the shipped Desk script with controlled time, DOM and network boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  let tick, desktopHook, request, resolveRequest;
  let renderedMenu;
  let calls = 0, reconnects = 0;
  const elements = new Map();
  const document = {
    hidden: false,
    documentElement: {getAttribute: () => 'light'},
    getElementById: id => elements.get(id),
    createElement: () => ({
      children: [], append(child) { this.children.push(child); },
      remove() { elements.delete(this.id); }
    }),
    body: {append: element => elements.set(element.id, element)}
  };
  const frappe = {
    csrf_token: 'test-csrf', boot: {passport: {expires: 1100, account_url: 'http://id.localhost:8080/account'}},
    realtime: {socket: {connected: false, connect: () => reconnects++}},
    session: {user: 'alice@example.com'}, avatar: () => '',
    utils: {is_rtl: () => false},
    ui: {create_menu: ({menu_items}) => { renderedMenu = menu_items; }}
  };
  const context = {
    document, frappe, Date: {now: () => 1000 * 1000}, window: {location: {}},
    setInterval: callback => { tick = callback; },
    $: () => ({html() {}, on: (event, callback) => {
      assert.equal(event, 'desktop_screen'); desktopHook = callback;
    }}),
    fetch: (url, options) => {
      calls++; request = {url, options};
      return new Promise(resolve => { resolveRequest = resolve; });
    }
  };
  vm.runInNewContext(fs.readFileSync('apps/passport_client/passport_client/public/js/passport_desk.js', 'utf8'), context);
  tick(); assert.equal(calls, 0);
  frappe.boot.passport.expires = 1050;
  document.hidden = true; tick(); assert.equal(calls, 0);
  document.hidden = false; tick(); tick(); assert.equal(calls, 1);
  assert.equal(request.url, '/passport/retry');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-Frappe-CSRF-Token'], 'test-csrf');
  resolveRequest({ok: false});
  await new Promise(setImmediate);
  assert.ok(elements.has('passport-blocked'));
  frappe.boot.passport.expires = 999;
  tick(); assert.equal(calls, 1);
  const retry = elements.get('passport-blocked').children[0].onclick();
  resolveRequest({ok: true, json: async () => ({expires: 1300})});
  await retry;
  assert.equal(frappe.boot.passport.expires, 1300);
  assert.equal(elements.size, 0);
  assert.equal(reconnects, 1);
  assert.equal(calls, 2); // Only validation requests; no application request is replayed.
  // Exercise the actual retained checkout's menu methods, not a mock toolbar API.
  const source = fs.readFileSync('.bench/apps/frappe/frappe/desk/page/desktop/desktop.js', 'utf8');
  const methods = source.slice(source.indexOf('\tsetup_avatar() {'), source.indexOf('\tsetup_navbar() {'));
  const desktop = vm.runInNewContext(`new class { constructor() {this.desktop_menu_items = [];} ${methods} }`, context);
  desktopHook(null, {desktop});
  desktopHook(null, {desktop});
  desktop.setup_avatar();
  const links = renderedMenu.filter(item => item.label === 'Frappe ID account');
  assert.equal(links.length, 1);
  assert.equal(links[0].url, frappe.boot.passport.account_url);
  delete frappe.boot.passport;
  const localDesktop = vm.runInNewContext(`new class { constructor() {this.desktop_menu_items = [];} ${methods} }`, context);
  desktopHook(null, {desktop: localDesktop});
  localDesktop.setup_avatar();
  assert.ok(!renderedMenu.some(item => item.label === 'Frappe ID account'));
  console.log('PASS Desk scheduling, background pause, single-flight renewal, failure/retry, reconnect and account link');
})().catch(error => { console.error(error); process.exitCode = 1; });
