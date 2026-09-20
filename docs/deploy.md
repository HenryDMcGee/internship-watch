# Deploying Internship Watch to Cloudflare

The dashboard and API run as one Cloudflare Worker named `internship-watch`, configured in `api/wrangler.toml`. It serves `https://internship-watch.mcgeehenry.com`, behind the Cloudflare Access email login.

You can deploy by hand once phase 1 is merged. From phase 4, merging a pull request deploys automatically.

## Before you start

- **Phase 1 is merged.** You need the API in `api/`, the dashboard in `frontend/`, and the migrations in `db/migrations/`.
- **The Access application exists** for exactly `internship-watch.mcgeehenry.com` (setup Step 3).
- **Node.js** (current LTS) is installed, for `wrangler`.
- **uv** is installed, for Python Workers. In PowerShell:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  Open a new terminal afterward.
- **Sign in to Cloudflare from the terminal**, once:
  ```powershell
  npx wrangler login
  ```

## One-time setup

### 1. Database ID

If you haven't created the database yet, run:
```powershell
npx wrangler d1 create internship-watch-db
```
Put the database ID in `api/wrangler.toml` as `database_id`. You can also find the ID in Cloudflare under **D1**. It isn't a secret.

### 2. Access details for login checks

The API checks the Access login token on every request, so it needs two values from Cloudflare. Put both in the `[vars]` section of `api/wrangler.toml`. Neither is a secret.
- **ACCESS_AUD:** in **Zero Trust**, open **Access controls**, **Applications**, then **Internship Watch**. Copy the **Application Audience (AUD) Tag**.
- **ACCESS_TEAM_DOMAIN:** your team domain, in the form `<team name>.cloudflareaccess.com`. It's shown in the Zero Trust settings.

### 3. DNS record for the subdomain

The Worker's route needs a proxied DNS record to answer on the subdomain. The record doesn't point at a real server.

In Cloudflare, open **mcgeehenry.com**, then **DNS**, then **Records**, and add:

| Type | Name | IPv6 address | Proxy status |
| --- | --- | --- | --- |
| AAAA | `internship-watch` | `100::` | **Proxied** (orange cloud) |

If a record named `internship-watch` already exists, remove it first. Leave every other record alone; they belong to your website.

## Deploy

Run these from the `api` folder.

### 4. Create the tables in the real database
```powershell
npx wrangler d1 migrations apply internship-watch-db --remote
```

### 5. Load your current data (first deploy only)
```powershell
cd ..
python scripts/seed.py --remote
cd api
```

### 6. Deploy the Worker

The Worker only ships files under `api/`, so refresh its copy of the titles
first (`/api/titles` reads that copy, never the repository root):
```powershell
cd ..
python scripts/bundle_titles.py
cd api
uv run pywrangler deploy
```

## Check it

1. Open `https://internship-watch.mcgeehenry.com` in a private browser window. It should ask you to sign in, then show the dashboard with your postings.
2. Open `https://mcgeehenry.com` and confirm your website looks the same as before.

## Deploying later changes

After merging a pull request:
1. If it added files in `db/migrations/`, run step 4.
2. If it changed `config/titles.json`, or you're not sure, run `python scripts/bundle_titles.py` from the repository root before step 6.
3. Run step 6.

To go back to the previous version:
```powershell
npx wrangler rollback
```
Rollback doesn't undo database migrations. Any pull request that changes the database says how to reverse it.

From phase 4, a GitHub Actions workflow runs steps 4 and 6 automatically when you merge, using `CLOUDFLARE_DEPLOY_TOKEN`.

## If something goes wrong

| What you see | Likely cause | Fix |
| --- | --- | --- |
| "Site can't be reached" | The DNS record from step 3 is missing | Add it, set to Proxied |
| The dashboard loads without asking you to sign in | The DNS record is set to DNS only, or the Access application's hostname doesn't match | Set the record to Proxied, and check the hostname is exactly `internship-watch.mcgeehenry.com` |
| Sign-in works but the dashboard shows errors or no data | `ACCESS_AUD` or `ACCESS_TEAM_DOMAIN` is wrong, or migrations weren't applied | Recheck step 2, then run step 4 |
| Deploy fails because of a Python package | The package doesn't run in Python Workers | Tell Claude; the plan's fallback is a small TypeScript Worker, proposed first |
| Your main website shows a Cloudflare login | The Access application was created for `mcgeehenry.com` instead of the subdomain | Fix the application's hostname in Zero Trust |
