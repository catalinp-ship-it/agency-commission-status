# Deploy to a URL (Streamlit Community Cloud — free)

This gets your dashboard onto a `https://...streamlit.app` URL in about 10 minutes. The code is already committed to a local git repo and is deploy-ready.

## 1. Put the code on GitHub

Create a new **empty** repo on GitHub (https://github.com/new) — name it e.g. `agency-commission-status`. Then, in a terminal in this folder, connect and push:

```bash
cd "/Users/Hyros/Claude/Projects/Agency commission status"
git remote add origin https://github.com/<your-username>/agency-commission-status.git
git branch -M main
git push -u origin main
```

> Your secrets are **not** in the repo — `.gitignore` excludes `.env` and `.streamlit/secrets.toml`. Only the code goes up.

The repo can be **private** on GitHub; Streamlit Cloud can still deploy it.

## 2. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `<your-username>/agency-commission-status`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Before clicking Deploy, open **Advanced settings → Secrets** and paste:

   ```toml
   FP_API_KEY = "your_real_firstpromoter_api_key"
   FP_ACCOUNT_ID = "your_real_account_id"
   # Optional: password-protect the dashboard
   # APP_PASSWORD = "choose-a-strong-password"
   ```

5. Click **Deploy**. First build takes a couple of minutes, then you get your URL.

That's it — open the URL and the app is live, pulling your real FirstPromoter data.

## Updating the app later

Any change you push to `main` redeploys automatically:

```bash
git add -A && git commit -m "update" && git push
```

## A note on access

You chose "anyone with the link," so the URL is public. The dashboard shows affiliate names, emails, and amounts owed. If you ever want to lock it down, you have two easy options:

- **Quick:** set `APP_PASSWORD` in the Streamlit secrets (step 4). Viewers then need the password. No code change needed — it's already wired in.
- **Proper:** in Streamlit Cloud, set the app to **Private** and add specific viewer emails (Settings → Sharing). Only those people can open it.

## Changing the API key or threshold later

Edit them in **Settings → Secrets** on Streamlit Cloud (no redeploy needed), or change `FP_THRESHOLD_DAYS` / `FP_CURRENCY` there too.
