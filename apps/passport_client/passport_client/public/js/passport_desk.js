// Validation never retries an application request or replays a write.
(() => {
	let busy = false;
	function notice() {
		if (document.getElementById('passport-blocked')) return;
		const panel = document.createElement('div');
		panel.id = 'passport-blocked';
		panel.style = 'position:fixed;bottom:1rem;right:1rem;z-index:9999;background:white;padding:1rem;border:1px solid #aaa;max-width:26rem';
		panel.textContent = 'Frappe ID validation is needed. Access stops at lease expiry. Interrupted actions will not be replayed. ';
		const button = document.createElement('button');
		button.textContent = 'Retry validation';
		button.onclick = validate;
		panel.append(button);
		document.body.append(panel);
	}
	async function validate() {
		if (busy) return;
		busy = true;
		try {
			const response = await fetch('/passport/retry', {
				method: 'POST', headers: {'X-Frappe-CSRF-Token': frappe.csrf_token}
			});
			if (!response.ok) throw new Error('Validation unavailable');
			const result = await response.json();
			frappe.boot.passport.expires = result.expires;
			document.getElementById('passport-blocked')?.remove();
			if (frappe.realtime?.socket && !frappe.realtime.socket.connected) frappe.realtime.socket.connect();
		} catch (_) { notice(); }
		finally { busy = false; }
	}
	setInterval(() => {
		if (document.hidden || !frappe.csrf_token || !frappe.boot?.passport) return;
		const remaining = frappe.boot.passport.expires - Date.now() / 1000;
		if (remaining <= 0) notice();
		else if (remaining <= 75) validate();
	}, 15000);
	$(document).on('desktop_screen', (_event, {desktop}) => {
		if (frappe.boot?.passport) {
			desktop.add_menu_item({
				label: 'Frappe ID account',
				url: frappe.boot.passport.account_url,
				icon: 'user',
			});
		}
	});
})();
