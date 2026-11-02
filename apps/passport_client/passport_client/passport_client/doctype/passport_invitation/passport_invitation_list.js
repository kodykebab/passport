frappe.listview_settings["Passport Invitation"] = {
	add_fields: ["user", "status", "expires_at"],
	refresh(listview) {
		if (
			listview.can_create &&
			!frappe.boot.read_only &&
			frappe.user.has_role("System Manager")
		) {
			listview.page.set_primary_action(__("Invite"), () =>
				listview.settings.primary_action(),
			);
		}
	},
	primary_action() {
		if (!frappe.user.has_role("System Manager")) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Invite to use Frappe ID"),
			fields: [
				{
					fieldname: "user",
					fieldtype: "Link",
					options: "User",
					label: __("Local account"),
					reqd: 1,
					get_query: () => ({
						filters: {
							enabled: 1,
							name: ["not in", ["Guest", "Administrator"]],
						},
					}),
					async onchange() {
						const user = dialog.get_value("user");
						dialog.set_value("email", "");
						if (!user) return;
						const result = await frappe.db.get_value("User", user, "email");
						if (dialog.get_value("user") === user)
							dialog.set_value("email", result.message.email);
					},
				},
				{
					fieldname: "email",
					fieldtype: "Data",
					label: __("Delivery address"),
					read_only: 1,
				},
				{
					fieldname: "notice",
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("The recipient signs in locally, confirms their Frappe ID and accepts. Application permissions stay the same. Invitations expire in seven days. An existing pending invitation opens without sending another email.")}</p>`,
				},
			],
			primary_action_label: __("Send invitation"),
			async primary_action(values) {
				const result = await frappe.call({
					method: "passport_client.invitations.send_invitation",
					args: { user: values.user },
					btn: dialog.get_primary_btn(),
				});
				dialog.hide();
				frappe.set_route("Form", "Passport Invitation", result.message.name);
			},
		});
		dialog.show();
	},
};
