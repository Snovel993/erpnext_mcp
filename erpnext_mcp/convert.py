# SPDX-License-Identifier: MIT
"""Turning a .docx into a PDF, using whatever the container happens to have.

WHY THIS IS THE ONE PLACE THIS APP SHELLS OUT. Everything under `render/` writes
its own bytes with the standard library, on purpose: a quarterly report is text
in boxes, the format is documented, and a dependency there would be a report that
works on the machine it was written on. Converting somebody else's Word document
is a different problem. A .docx is a zip of XML that encodes a layout engine's
worth of decisions — styles, numbering, tables, section breaks, fonts — and
reimplementing enough of that to lay it out on a page is not a few hundred lines
of zipfile. It is LibreOffice. So this asks LibreOffice.

WHICH MEANS IT CAN BE ABSENT, AND THAT IS HANDLED RATHER THAN ASSUMED.
`available()` reports what this host actually has, by name, and the tool that
uses it refuses with the package to install rather than dying inside a
subprocess call. Nothing here installs anything at runtime: a tool that
apt-gets a package mid-request is a tool that can hang a worker for a minute and
leave a container different from the image it was built from.

TWO ROUTES, AND THE ORDER IS NOT THE OBVIOUS ONE. `docx2pdf` is the pip package
whose name says what it does, and it drives Microsoft Word through COM on Windows
or AppleScript on macOS — so on the Linux container this app actually runs in, it
does nothing at all. LibreOffice headless is the workhorse and is tried first;
docx2pdf is the fallback for a developer converting on their own Mac.

THE PROFILE DIRECTORY IS NOT OPTIONAL. `soffice` writes a user profile on first
run, and in a container running as an unprivileged user with no writable HOME it
fails there rather than at the conversion — with a message about a missing
configuration that says nothing about what went wrong. So every invocation points
`-env:UserInstallation` at a directory inside the temp dir it just made, which it
therefore owns. This is the same lesson as "a script that runs outside the bench
must make its own log directories before it connects": a process that assumes
somebody else created a writable directory for it is a process that works in
development and fails in the image.
"""

import os
import shutil
import subprocess
import tempfile

#: Names a LibreOffice binary goes by, in the order worth trying. `soffice` is
#: what the Debian and Alpine packages install; the macOS bundle path is last
#: because it only exists on a developer's laptop.
_SOFFICE_NAMES = ("soffice", "libreoffice", "soffice.bin")
_MACOS_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

#: Longest a conversion may run. A governance document is a few pages; a minute
#: is already generous, and the ceiling exists so a malformed file cannot pin a
#: Frappe worker until somebody notices.
TIMEOUT_SECONDS = 120

#: What to tell an operator whose container has neither route.
INSTALL_HINT = (
	"install libreoffice-headless (apt: `libreoffice-writer --no-install-recommends`, "
	"alpine: `libreoffice-writer`) into the container, or `pip install docx2pdf` on a host "
	"that has Microsoft Word. Nothing is installed at runtime: a tool that fetched a package "
	"mid-request would hang the worker and leave the container different from its image."
)


def soffice_binary() -> str:
	"""The LibreOffice executable on this host, or "" if there is none."""
	for name in _SOFFICE_NAMES:
		found = shutil.which(name)
		if found:
			return found
	return _MACOS_SOFFICE if os.path.exists(_MACOS_SOFFICE) else ""


def docx2pdf_available() -> bool:
	try:
		import docx2pdf  # noqa: F401
	except Exception:
		return False
	return True


def available() -> dict:
	"""What this host can convert with, in words a refusal can use verbatim.

	Returns `{"ready", "via", "binary", "detail"}`. Never raises and never runs a
	conversion — this is the question `dry_run` and the tool's own refusal ask,
	and both of them have to be cheap.
	"""
	binary = soffice_binary()
	if binary:
		return {
			"ready": True,
			"via": "libreoffice",
			"binary": binary,
			"detail": f"LibreOffice headless at {binary}",
		}
	if docx2pdf_available():
		return {
			"ready": True,
			"via": "docx2pdf",
			"binary": "",
			"detail": (
				"the docx2pdf package, which drives Microsoft Word. Fine on a Mac or a Windows "
				"host; it does nothing on a Linux container, so a server should have LibreOffice."
			),
		}
	return {
		"ready": False,
		"via": "",
		"binary": "",
		"detail": f"no converter on this host — {INSTALL_HINT}",
	}


class ConversionError(RuntimeError):
	"""A conversion that was attempted and did not produce a PDF."""


def docx_to_pdf(content: bytes, file_name: str = "document.docx") -> bytes:
	"""The .docx bytes in, the PDF bytes out.

	Raises `ConversionError` with something a person can act on: which route was
	tried, what it said, and — for the commonest failure, an empty output — that
	the input may not be a .docx at all.
	"""
	route = available()
	if not route["ready"]:
		raise ConversionError(f"no .docx-to-PDF converter on this host. {INSTALL_HINT}")
	stem = _safe_stem(file_name)
	with tempfile.TemporaryDirectory(prefix="erpnext-mcp-convert-") as workdir:
		source = os.path.join(workdir, f"{stem}.docx")
		with open(source, "wb") as handle:
			handle.write(content)
		target = os.path.join(workdir, f"{stem}.pdf")
		if route["via"] == "libreoffice":
			_run_soffice(route["binary"], source, workdir)
		else:
			_run_docx2pdf(source, target)
		if not os.path.exists(target):
			produced = sorted(name for name in os.listdir(workdir) if name.endswith(".pdf"))
			if not produced:
				raise ConversionError(
					f"{route['detail']} ran and produced no PDF. The commonest cause is that the "
					"file is not really a .docx — a .doc renamed, or a PDF with the wrong "
					"extension. Check what the attachment actually is."
				)
			target = os.path.join(workdir, produced[0])
		with open(target, "rb") as handle:
			pdf = handle.read()
	if not pdf.startswith(b"%PDF"):
		raise ConversionError(
			f"{route['detail']} produced {len(pdf)} bytes that are not a PDF (a PDF starts with "
			"'%PDF'). Nothing was attached."
		)
	return pdf


def _safe_stem(file_name: str) -> str:
	"""A filename stem safe to hand to a subprocess in a directory we made.

	The name comes off a File record, which came off whatever somebody uploaded.
	It never reaches a shell — the command is a list — but it does become a path,
	so anything that could climb out of the temp directory is dropped rather than
	escaped.
	"""
	stem = os.path.splitext(os.path.basename(str(file_name or "")))[0]
	cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in stem)
	return cleaned.strip("_") or "document"


def _run_soffice(binary: str, source: str, workdir: str) -> None:
	profile = os.path.join(workdir, "loprofile")
	os.makedirs(profile, exist_ok=True)
	command = [
		binary,
		f"-env:UserInstallation=file://{profile}",
		"--headless",
		"--norestore",
		"--invisible",
		"--convert-to",
		"pdf",
		"--outdir",
		workdir,
		source,
	]
	try:
		completed = subprocess.run(  # noqa: S603 - a list, no shell, a path we created
			command,
			capture_output=True,
			timeout=TIMEOUT_SECONDS,
			check=False,
		)
	except subprocess.TimeoutExpired:
		raise ConversionError(
			f"LibreOffice did not finish converting within {TIMEOUT_SECONDS} seconds and was "
			"stopped. A governance document is a few pages; something that takes longer is "
			"either enormous or malformed. Nothing was attached."
		) from None
	except OSError as exc:
		raise ConversionError(f"could not run {binary}: {type(exc).__name__}: {exc}") from None
	if completed.returncode != 0:
		detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace").strip()
		raise ConversionError(
			f"LibreOffice exited {completed.returncode}: {detail[:400] or '<no output>'}"
		)


def _run_docx2pdf(source: str, target: str) -> None:
	import docx2pdf

	try:
		docx2pdf.convert(source, target)
	except Exception as exc:
		raise ConversionError(f"docx2pdf failed: {type(exc).__name__}: {exc}") from None
