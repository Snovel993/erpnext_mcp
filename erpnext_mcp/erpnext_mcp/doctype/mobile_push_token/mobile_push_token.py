# SPDX-License-Identifier: MIT
"""Controller for Mobile Push Token — where a break horn is delivered to.

WHY THE DEVICE IS THE IDENTITY AND THE TOKEN IS NOT. Apple issues a new APNs
device token when the app is reinstalled, when a phone is restored from a
backup, when the device is migrated to new hardware, and periodically for no
reason the client is ever told. A register keyed on the token accumulates one
live row and five dead ones per handset, and every crew push becomes five
wasted requests and one delivery — with no way to tell from the register which
was which. So `device_key` (platform::device_id) is unique, and the token is a
mutable field on the row that key finds.

WHY `device_key` IS COMPUTED HERE AND NOT SENT. A Frappe DocType cannot declare
a unique index over two columns, so the composite key has to be one column; and
a column a caller could set is not a key, because two clients spelling the
joiner differently would produce two rows for one phone. The controller writes
it on every save from the two fields it is made of, so a row whose platform was
corrected in the Desk gets a corrected key rather than a stale one.

WHY NOTHING HERE IS DELETED. `is_active` retires a row: cleared on logout, and
cleared by the sender when Apple answers Unregistered or BadDeviceToken. A
device that stopped receiving is the fact somebody needs when a worker reports
that the horn never reaches them, and a deleted row answers no question at all.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The platforms this app knows how to address. Lower case is the stored form —
#: it is what the handset sends, it is what the payload builder switches on, and
#: a register holding both "iOS" and "ios" would hold two device keys for one
#: phone, which is the exact failure `device_key` exists to prevent.
PLATFORMS = ("ios", "android")

#: The joiner. Two colons rather than one because a device identifier containing
#: a single colon would otherwise be able to imitate another platform's key.
KEY_JOINER = "::"

#: How long a device identifier may be. Apple's `identifierForVendor` is a
#: 36-character UUID and an FCM instance id is under 256; the cap is here so a
#: client sending a kilobyte of junk is refused at the door rather than
#: truncated into a key that collides with somebody else's.
MAX_DEVICE_ID = 128


def device_key(platform, device_id: str) -> str:
	"""The composite key, from the two fields it is made of. Pure; never raises."""
	return f"{str(platform or '').strip().lower()}{KEY_JOINER}{str(device_id or '').strip()}"


class MobilePushToken(Document):
	def validate(self):
		self.platform = str(self.platform or "").strip().lower()
		if self.platform not in PLATFORMS:
			frappe.throw(
				_("platform must be one of {0}. Got {1}.").format(
					", ".join(PLATFORMS), self.platform or "nothing"
				)
			)

		self.device_id = str(self.device_id or "").strip()
		if not self.device_id:
			frappe.throw(
				_(
					"Device ID is required. A push token with no device to belong to cannot be "
					"replaced when the token rotates, which is the one thing this register is for."
				)
			)
		if len(self.device_id) > MAX_DEVICE_ID:
			frappe.throw(
				_("Device ID is {0} characters; the maximum is {1}.").format(
					len(self.device_id), MAX_DEVICE_ID
				)
			)

		self.token = str(self.token or "").strip()
		self.device_key = device_key(self.platform, self.device_id)

		if self.employee and not self.employee_name:
			self.employee_name = frappe.db.get_value("Employee", self.employee, "employee_name") or None

		if not self.registered_at:
			self.registered_at = frappe.utils.now()
