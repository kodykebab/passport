// Validation never retries an application request or replays a write.
(() => {
	let busy = false;
	let unmountNotice;
	async function notice() {
		if (document.getElementById('passport-blocked')) return;
		const panel = document.createElement('div');
		panel.id = 'passport-blocked';
		panel.style = 'position:fixed;bottom:1rem;right:1rem;left:1rem;margin-left:auto;z-index:9999;max-width:26rem';
		document.body.append(panel);
		try {
			const {mountNotice} = await import(frappe.boot.passport.ui.script);
			const unmount = await mountNotice(panel, validate, frappe.boot.passport.ui.style);
			if (panel.isConnected) unmountNotice = unmount;
			else unmount();
		} catch (_) {
			window.location.assign('/passport/status');
		}
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
			unmountNotice?.();
			unmountNotice = null;
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
		if (!frappe.boot?.passport && frappe.boot?.passport_link_url) {
			desktop.add_menu_item({
				label: 'Link Frappe ID',
				url: frappe.boot.passport_link_url,
				icon: 'user',
			});
		}
		if (frappe.boot?.passport) {
			desktop.add_menu_item({
				label: 'Frappe ID account',
				url: frappe.boot.passport.account_url,
				icon: 'user',
			});
		}
	});
})();
