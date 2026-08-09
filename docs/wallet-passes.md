# Wallet passes — what to obtain, and where to put it

`generate_employee_badge_pass` (v0.53.0) turns an employee badge into an **Apple
Wallet `.pkpass`** file and a **Google Wallet save link**. The code is complete
and works today; what it cannot do without credentials is *sign*. This document
is the list of things to obtain and the exact `site_config.json` keys to put them
in. **Nothing in the app changes when the certificates arrive** — the same call
starts signing.

Until then the tool builds a **complete but unsigned** pass and says so:
`apple.signed: false`, with the missing keys named in `apple.requires`. Apple
Wallet will refuse to open an unsigned pass. That refusal is deliberate — see
the header of `erpnext_mcp/wallet.py` for why a self-signed placeholder would be
worse than an honest one.

---

## Part 1 — Apple Wallet

You need three things: a **Pass Type ID**, a **certificate** for it, and
**Apple's WWDR intermediate**. All of it requires a paid Apple Developer Program
membership ($99/year), which the farm app already has for TestFlight.

### 1.1 Register a Pass Type ID

1. Sign in at <https://developer.apple.com/account/resources/identifiers/list>.
2. **Identifiers → + → Pass Type IDs → Continue.**
3. Description: `Farm Ops Employee Badge`. Identifier: something under your own
   reverse-DNS, e.g. **`pass.farm.fafo.badge`**. It must begin with `pass.`.
4. Register.

Write the identifier down — it becomes `apple_wallet_pass_type_identifier`, and
it is baked into every pass. **Changing it later invalidates every pass already
on a worker's phone**, so pick one you can live with.

### 1.2 Find the Team ID

Top right of the developer account page, or **Membership Details** — a
ten-character string like `A1B2C3D4E5`. It becomes
`apple_wallet_team_identifier`.

### 1.3 Create the certificate

On a Mac:

1. **Keychain Access → Certificate Assistant → Request a Certificate From a
   Certificate Authority.** Enter your email, leave CA Email blank, choose
   **Saved to disk**. This produces a `.certSigningRequest` file.
2. Back in the developer portal: **Identifiers → your Pass Type ID → Configure /
   Create Certificate**, upload the CSR, download the resulting `pass.cer`.
3. Double-click `pass.cer` to install it into the login keychain.
4. In Keychain Access find **"Pass Type ID: pass.farm.fafo.badge"**, expand it so
   both the certificate *and* its private key are selected, right-click →
   **Export 2 items…**, save as **`pass.p12`**, and set a password.

That `.p12` holds the certificate, the private key and (usually) the WWDR
intermediate that signed it. It is the whole credential.

### 1.4 Get the WWDR intermediate

Download **Worldwide Developer Relations — G4** from
<https://www.apple.com/certificateauthority/> (file `AppleWWDRCAG4.cer`).

You only need to configure this separately if the `.p12` did not include it —
`sign_manifest` uses the copy inside the bundle when there is one. Configuring it
anyway is harmless.

### 1.5 Install it on the site

Put the files somewhere the bench user can read and nothing else can — the site's
private directory is the natural home:

```bash
mkdir -p sites/<site>/private/wallet
cp pass.p12 AppleWWDRCAG4.cer sites/<site>/private/wallet/
chmod 600 sites/<site>/private/wallet/pass.p12
```

Then add to **`sites/<site>/site_config.json`**:

```json
{
  "apple_wallet_pass_type_identifier": "pass.farm.fafo.badge",
  "apple_wallet_team_identifier": "A1B2C3D4E5",
  "apple_wallet_certificate": "/home/frappe/frappe-bench/sites/<site>/private/wallet/pass.p12",
  "apple_wallet_certificate_password": "the password you set on export",
  "apple_wallet_wwdr_certificate": "/home/frappe/frappe-bench/sites/<site>/private/wallet/AppleWWDRCAG4.cer"
}
```

`bench restart`, then call the tool. `apple.signed` should now be `true` and
`apple.warnings` empty.

> **Why `site_config.json` and not the settings form.** This is a private signing
> key and the path to it. A Single doctype is editable by anybody who reaches the
> Desk with the right role and is dumped in full by several Frappe debug paths;
> `site_config.json` is a file only whoever deploys the site can write. Same
> reasoning as `farm_ops_mobile_enabled`.

### 1.6 Optional appearance keys

| Key | Default | Meaning |
| --- | --- | --- |
| `apple_wallet_organization_name` | the Company name | Shown by iOS on the lock screen |
| `apple_wallet_background_color` | `rgb(31,77,43)` | Card colour. `rgb(r,g,b)` or `#rrggbb` |
| `apple_wallet_foreground_color` | `rgb(255,255,255)` | Field values |
| `apple_wallet_label_color` | `rgb(190,214,196)` | Field labels |

A colour typed wrong falls back to the default rather than failing the build.

### 1.7 The PEM alternative (self-signed, development only)

For exercising the signing path before the Apple certificate exists:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout dev-key.pem -out dev-cert.pem \
  -subj "/CN=Pass Type ID: pass.farm.fafo.badge"
```

```json
{
  "apple_wallet_certificate": "/…/dev-cert.pem",
  "apple_wallet_private_key": "/…/dev-key.pem"
}
```

The pass will be signed with a certificate Apple did not issue. **Wallet will
still refuse to open it.** This proves the plumbing, nothing more.

---

## Part 2 — Google Wallet

Android has no equivalent of the AirDropped file. A Google Wallet pass is a JSON
object signed into a JWT that becomes a `pay.google.com/gp/v/save/…` link.

### 2.1 Get an issuer account

1. Go to the **Google Wallet Business Console**:
   <https://pay.google.com/business/console>
2. Sign up as an issuer. Note the **Issuer ID** — a long number like
   `3388000000012345678`. It becomes `google_wallet_issuer_id`.
3. New issuer accounts are in **demo mode**: passes work, but only for accounts
   you add under **Users → test accounts**. Request publishing access when you
   are ready for real workers.

### 2.2 Create a service account

1. In the **Google Cloud Console** for the same organisation, enable the **Google
   Wallet API**.
2. **IAM & Admin → Service Accounts → Create.** Name it e.g.
   `farm-wallet-signer`. No project roles are needed.
3. **Keys → Add Key → Create new key → JSON.** Download it.
4. Back in the Wallet Business Console: **Users → Invite a user**, paste the
   service account's email (`…@….iam.gserviceaccount.com`), role **Developer**.
   *Without this step the issuer will not accept passes it signs.*

### 2.3 Install it on the site

```bash
cp farm-wallet-signer-*.json sites/<site>/private/wallet/google-service-account.json
chmod 600 sites/<site>/private/wallet/google-service-account.json
```

```json
{
  "google_wallet_issuer_id": "3388000000012345678",
  "google_wallet_service_account": "/home/frappe/frappe-bench/sites/<site>/private/wallet/google-service-account.json",
  "google_wallet_origins": ["https://erp.yourfarm.example"],
  "google_wallet_class_suffix": "farm_employee_badge",
  "google_wallet_background_color": "#1f4d2b"
}
```

### 2.4 Images on the Android pass need a public HTTPS URL

**Google fetches pass images itself, over the public internet.** It is not sent
them. That has two consequences the Apple half does not have:

- A photograph at `/private/files/…` — which is what `set_employee_photo`
  correctly writes, because it is a picture of a person — **cannot be on a Google
  Wallet pass**. The tool leaves it out and says so in `google.warnings` rather
  than writing a URL that 403s.
- The site needs an HTTPS address Google can reach. Set **Public URL** in ERPNext
  MCP Settings; a plain-HTTP address is refused by Google for image URIs and the
  tool will not use one.

The Apple `.pkpass` carries its pixels inside the archive and is unaffected by
either.

To put a farm logo on the Android pass, upload it as a **public** File (not
private) and set it as `Company.badge_logo`.

---

## Part 3 — Using it

### From the Desk / MCP

```json
{"employee": "HR-EMP-00042", "company": "Cherry Farm LLC", "platform": "both"}
```

The `.pkpass` is attached privately to the Employee; `apple.file_url` points at
it. Regenerating replaces that one file.

### From the handset

`POST /farmops/api/mobile/get_employee_badge_pass` with
`{"employee": "HR-EMP-00042", "company": "Cherry Farm LLC"}`.

The answer carries `apple.pkpass_base64` — the bytes themselves, because a phone
authenticating with `X-FarmOps-Token` cannot fetch a private `file_url`. The iOS
side should:

1. Decode the base64 and write it to a temporary file named
   `apple.file_name` (`badge-CFL-0001.pkpass`).
2. Present it with `UIActivityViewController` for AirDrop, **or** open it
   directly with `PKAddPassesViewController` when the badge is for the holder of
   this phone.
3. Check `apple.signed` first. An unsigned pass will fail to open, and telling
   the foreman that is better than watching Wallet reject it silently.
4. For Android, share `google.save_url` as a link — it opens Google Wallet.

The `content_type` in the answer (`application/vnd.apple.pkpass`) is what makes
iOS route the file to Wallet rather than to Files.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `apple.signed: false`, `requires` names keys | No certificate configured yet — Part 1. |
| `could not be signed (ValueError: … is not a file …)` | Path in `site_config.json` is wrong, or the bench user cannot read it. |
| `could not be signed (ValueError: Invalid password …)` | `apple_wallet_certificate_password` does not match the `.p12`. |
| Wallet: "Cannot install pass" with a real certificate | The Pass Type ID in `site_config.json` does not match the one the certificate was issued for. They must be identical. |
| Wallet opens the pass but shows no photo | The Employee has no `image`, or it could not be decoded — check `warnings`. |
| Google save link 401s | The service account was not invited as a Developer on the issuer account — Part 2.2 step 4. |
| Google pass has no images | Expected unless the files are public and Public URL is set — Part 2.4. |

## Reissue and revocation

A wallet pass is **not** independently revocable — there is no push service
configured, so a pass already on a phone stays there. What makes that safe is
that the pass carries only `badge_id`, and revocation happens in the register:
`generate_employee_badge_pass(regenerate=true)` or
`link_badge_to_employee(active=false)` retires the badge, and `resolve_badge`
then refuses it. A worker holding a retired pass scans a badge that resolves to
nobody — which is exactly what happens with a retired laminated card.

Adding push updates would mean a `webServiceURL`, an `authenticationToken` per
pass and a registration endpoint. It is a real feature and it is not in v0.53.0.
