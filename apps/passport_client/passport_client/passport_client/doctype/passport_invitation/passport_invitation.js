frappe.ui.form.on("Passport Invitation", {
	refresh(frm) {
		frm.disable_save();
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
		frm.add_custom_button(__("View email queue"), () => {
			frappe.set_route("List", "Email Queue", {
				reference_doctype: "Passport Invitation",
				reference_name: frm.doc.name,
			});
		});
		if (frm.doc.status !== "Pending") return;
		frm.add_custom_button(__("Cancel invitation"), () => {
			frappe.confirm(
				__(
					"Cancel this invitation? A queued email may still arrive, but it cannot be accepted.",
				),
				async () => {
					await frappe.call({
						method: "passport_client.invitations.cancel_invitation",
						args: { name: frm.doc.name },
						freeze: true,
					});
					frm.reload_doc();
				},
			);
		});
	},
});
