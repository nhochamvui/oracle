# Setup guide — OCI "Always Free" ARM retry on GitHub Actions

Repo: <https://github.com/nhochamvui/oracle>

This repo holds three files:

| File | Purpose |
|------|---------|
| `.github/workflows/oci-arm-retry.yml` | Sweeps OCI every 30 min, launching `VM.Standard.A1.Flex` (ARM) the moment capacity appears. |
| `.github/workflows/keepalive.yml` | One commit/month so GitHub doesn't disable the schedule after 60 days of inactivity. |
| `retry-sdk.py` | The script the workflow runs. You fill in your OCIDs here. |

You don't need to install anything locally. Everything is done in the browser.

---

## Part 1 — Create an OCI API key (Console only, no installs)

1. Sign in at <https://cloud.oracle.com>.
2. Top-right **avatar → My profile**.
3. Copy your **User OCID** (shown on the Profile page).
4. Top-right **avatar → Tenancy: <your tenancy>** and copy the **Tenancy OCID**.
5. Back in **My profile**, scroll to **Resources → API keys** → click **Add API key**.
6. Choose **Generate API key pair** → click **Download private key** → save it somewhere safe as `oci_api_key.pem`.
   (You can't download it again — if you lose it, make a new key.)
7. Click **Add**. The Console now shows a **fingerprint** — that's expected.
8. Copy the **Configuration File Preview** block. This becomes your `OCI_CONFIG` secret. It looks like:

   ```ini
   [DEFAULT]
   user=ocid1.user.oc1..xxxxxxxx
   fingerprint=aa:bb:cc:dd:...
   tenancy=ocid1.tenancy.oc1..xxxxxxxx
   region=<your home region>
   key_file=/home/YOU/.oci/oci_api_key.pem
   ```

   The `key_file` line doesn't matter — the workflow rewrites it to the runner's path automatically.

9. Make sure `region` **is your home region** (Always Free resources only exist there).

---

## Part 2 — Collect the values for `retry-sdk.py`

You need three OCIDs; the availability domains are auto-discovered (leave that list empty).

| Value | Where to get it |
|-------|-----------------|
| `COMPARTMENT_ID` | **Identity & Security → Compartments** → click your compartment → copy OCID. For a personal account you can even use the **Tenancy OCID**. |
| `SUBNET_ID` | **Networking → Virtual Cloud Networks → <your VCN> → Subnets** → click a **public** subnet → copy OCID. No VCN yet? **Start VCN Wizard → "VCN with Internet Connectivity"** → Create. |
| `IMAGE_ID` | An **aarch64 (arm64)** image OCID for your region. Open <https://docs.oracle.com/iaas/images/> → pick a Ubuntu family (e.g. `ubuntu-2404`) → find the **aarch64** entry for your region → copy its OCID (starts with `ocid1.image...`). |
| `AVAILABILITY_DOMAINS` | Leave as `[]` — the script discovers all ADs itself. |
| `OCPUS` / `MEMORY_GB` | Leave at `2` / `12` (the Always Free limit). |

---

## Part 3 — Add the two GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

| Name | Value |
|------|-------|
| `OCI_CONFIG` | The **entire config** from Part 1 (including the `[DEFAULT]` line). |
| `OCI_API_KEY` | The **entire contents of `oci_api_key.pem`** (including the `-----BEGIN ... -----` / `-----END ... -----` lines). |

Secrets are never printed in logs. Even though this repo is public, forks don't receive your secrets — they're safe here.

---

## Part 4 — Let the keepalive job push

Repo → **Settings → Actions → General → Workflow permissions** → select **Read and write permissions** → **Save**.

(Without this, the monthly keepalive commit fails and GitHub may eventually disable the schedule.)

---

## Part 5 — Fill in `retry-sdk.py`

Open `retry-sdk.py` in the repo → pencil (Edit) → replace the `CHANGEME` values with your three OCIDs from Part 2 → **Commit changes**. Keep `OCPUS=2`, `MEMORY_GB=12`.

---

## Part 6 — Test it

1. Repo → **Actions** tab. If prompted, click **I understand my workflows, go ahead and enable them**.
2. Left sidebar → **oci-arm-retry** → **Run workflow** → **Run workflow**.
3. Open the running job and read the **"Try to launch the free ARM instance"** step:
   - `no capacity yet` → ✅ everything works; it's just waiting for capacity.
   - `SUCCESS: launched ocid1.instance...` → 🎉 you got it.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Red at "Write OCI credentials" | `OCI_CONFIG` or `OCI_API_KEY` secret missing/empty — re-add them. |
| `NotAuthenticated` / HTTP 401 | Fingerprint/key mismatch — regenerate the API key in the Console. |
| `NotAuthorized` / HTTP 403 | Wrong compartment OCID, or a missing IAM policy. |
| `LimitExceeded` | You already used the free compute quota or already have an instance. |
| `no capacity yet` forever | Normal for free tenancies — keep waiting, or upgrade to Pay-As-You-Go for priority. |
| Schedule silently stopped | Keepalive needs write permission (Part 4). |

---

## Long-term notes

- The schedule is **every 30 min**. This is a **public** repo, so Actions minutes are **unlimited** — you can tighten the `cron:` line in `oci-arm-retry.yml` if you want more attempts per hour.
- `VM.Standard.A1.Flex` is the target (Always Free: **2 OCPU / 12 GB** since Oracle halved the allowance in 2026). The Always Free AMD micro lives under the Console's **"Specialty and previous generation"** shape card.
- Optional but recommended: **upgrade to Pay-As-You-Go** for capacity priority. Stay inside the Always Free limits → still $0.

---

## Security

- **Never commit** `oci_api_key.pem` or your `~/.oci/config`. They belong only in Actions secrets.
- If a key leaks: Console → **My profile → API keys** → delete it and create a new one.
