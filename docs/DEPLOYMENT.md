# Deploying Swachh Netra online

```
 Laptop / Raspberry Pi                 Render                              Vercel
 ┌──────────────────────┐   HTTPS   ┌──────────────────────────┐  HTTPS  ┌───────────────────────┐
 │ edge pipeline        │ ────────► │ Flask API + SQLite       │ ◄────── │ React dashboard       │
 │ (YOLO, plate OCR)    │ X-API-Key │ evidence files on a disk │  login  │ (static site)         │
 └──────────────────────┘           └──────────────────────────┘         └───────────────────────┘
```

* **Vercel** hosts the dashboard: a static site, free.
* **Render** hosts the API server. Vercel can't: its Python functions have no permanent disk for the SQLite database and evidence photos/clips.
* The **AI pipeline** stays on the laptop or Raspberry Pi. It is far too large for serverless hosting, and it needs the camera.

You'll need free accounts on [github.com](https://github.com) (you have one), [render.com](https://render.com) and [vercel.com](https://vercel.com). Sign in to both with GitHub.

---

## Step 1: API server on Render (about 5 minutes)

1. Render dashboard → **New** → **Blueprint** → pick the `Pedestrian-detection` repository. Render reads `render.yaml` from the repo.
2. Check the plan:

   | Plan | Persistent disk | What happens to data |
   |---|---|---|
   | **Starter** (paid, the default in `render.yaml`) | yes, 1 GB at `/var/data` | Kept across restarts and redeploys. Use this for anything real. |
   | **Free** | no | Wiped whenever the service restarts, redeploys or sleeps (free services sleep after a period without visits, and the next visit takes up to a minute to wake). Fine for a demo; `LW_SEED_SAMPLE` refills it with sample cases. |

   For the free plan, edit `render.yaml` before creating the Blueprint: set `plan: free` and delete the `disk:` block.
3. Click **Apply**. The build installs only `server/requirements.txt` (Flask, gunicorn, tzdata), so it takes about a minute.
4. When it's live, note two things:
   * the **URL**, e.g. `https://swachh-netra-api.onrender.com`. Open `<URL>/api/v1/health` and you should see `{"ok": true, ...}`.
   * **Environment** tab → the generated values of **`LW_API_KEY`** (for the camera device) and **`LW_DASHBOARD_PASSWORD`** (the access code officers type into the dashboard). You can replace either with your own value.

## Step 2: dashboard on Vercel (about 3 minutes)

1. Vercel dashboard → **Add New…** → **Project** → import the `Pedestrian-detection` repository.
2. **Root Directory**: click *Edit* and choose **`dashboard`**. Vercel then detects Vite; `dashboard/vercel.json` sets the build and the page routing.
3. **Environment Variables**: add
   `VITE_API_BASE` = your Render URL from step 1 (no trailing slash).
4. Click **Deploy**. Note the address, e.g. `https://swachh-netra.vercel.app`.

`VITE_API_BASE` is built into the site. If you change it later, redeploy (Deployments → ⋯ → Redeploy).

## Step 3: connect the two

Back on Render → your service → **Environment**: set **`LW_CORS_ORIGINS`** to your exact Vercel address, e.g. `https://swachh-netra.vercel.app`, and save (the service restarts).

`render.yaml` starts it as `https://*.vercel.app`, so it works right away, but that allows any Vercel site to call your API from a browser. Set the exact address once you know it. Separate several addresses with commas, e.g. to keep Vercel preview deployments working.

Open the Vercel address. The sign-in screen asks for the access code (`LW_DASHBOARD_PASSWORD`).

## Step 4: point the camera device at the online server

In `edge/config.yaml` on the laptop / Pi:

```yaml
server:
  url: https://swachh-netra-api.onrender.com   # your Render URL
  api_key: <LW_API_KEY from Render>
```

Run it as usual (`python -m edge.run --source ...`). Incidents appear on the Vercel dashboard within seconds.

---

## What works where

| Feature | Local (laptop) | Online (Vercel + Render) |
|---|---|---|
| Dashboard, register, case review, notices | ✅ | ✅ (behind the access code) |
| Camera pipeline uploading incidents | ✅ | ✅ (to the Render URL) |
| **Test with your own footage** upload panel | ✅ | ❌ hidden (`LW_DEV_TOOLS=0`): processing needs the AI libraries, so use it locally |
| **Live camera** page | ✅ | ❌ The live feed comes straight from the device (`http://localhost:8090`), which an online HTTPS page can't show. Incidents still arrive normally. |

## Settings reference (Render → Environment)

| Variable | Meaning |
|---|---|
| `LW_API_KEY` | Secret the camera device sends (`server.api_key` in `edge/config.yaml`) |
| `LW_DASHBOARD_PASSWORD` | Dashboard access code. Empty = no login (only acceptable on a private network). Changing it signs everyone out. |
| `LW_CORS_ORIGINS` | Website(s) allowed to use the API from a browser: your Vercel address(es); `*` wildcards allowed |
| `LW_DATA_DIR` | Where the database and evidence live (`/var/data` = the disk) |
| `LW_SEED_SAMPLE` | Number of sample cases to add when the database is empty (`0` = none) |
| `LW_DEV_TOOLS` | Keep `0` online |
| `LW_MUNICIPALITY` | Name printed on the dashboard and notices |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "Cannot reach the server" | `VITE_API_BASE` wrong or missing: check it on Vercel and **redeploy**. Free Render services may take up to a minute to wake; reload. |
| Browser console shows a CORS error | `LW_CORS_ORIGINS` on Render must contain the exact Vercel address (`https://…`, no trailing slash). |
| "Wrong access code" | Copy `LW_DASHBOARD_PASSWORD` from Render → Environment. |
| Camera uploads fail with HTTP 401 | `server.api_key` in `edge/config.yaml` ≠ `LW_API_KEY` on Render. |
| All cases disappeared | Free plan without a disk: the service restarted. Use the Starter plan with the disk for data that must persist. |
| Deep links (e.g. `/incidents/12`) give 404 on Vercel | The project's Root Directory must be `dashboard`, so that `dashboard/vercel.json` is used. |

## Checked before release

This setup was rehearsed locally:
* the server ran under gunicorn in a fresh environment with only `server/requirements.txt` installed;
* the dashboard was built with `VITE_API_BASE` pointing at it and served from a different address;
* the edge pipeline uploaded to it with the API key.

In a browser, all of this worked: a wrong code was refused; after sign-in the data and all evidence images loaded across the two sites; **Plate verified** worked; deep links loaded; sign-out returned to the login screen. There were no failed requests. The actual Render and Vercel accounts were not used for this test.
