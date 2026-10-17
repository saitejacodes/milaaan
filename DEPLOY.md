# Deploying the Milaan web demo

The demo is a Streamlit app (`milaan/webapp.py`) packaged with a `Dockerfile`.
It needs **no API key, no database server and no paid service**. Every number
it shows is computed when the server starts.

Recommended host: **Hugging Face Spaces (Docker)**, which is free and gives a URL like
`https://huggingface.co/spaces/<you>/milaan`.

| Option | Cost | Cold start | Notes |
|---|---|---|---|
| **Hugging Face Spaces (Docker)** ✅ | Free (2 vCPU, 16 GB RAM) | ~30–60 s after 48 h idle | Known to AI/ML reviewers; full Docker control |
| Streamlit Community Cloud | Free | Sleeps after ~12 h idle | Easiest, but no Dockerfile and less control |
| Render / Railway | Free tier limited | Render free sleeps after 15 min | Better for an API than a Streamlit app |

---

## 0. Run it locally first (2 minutes)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dashboard]'
streamlit run milaan/webapp.py
```

Open http://localhost:8501. The first load takes about 3 s while it runs the
benchmark, the agent suite and 64 attacks.

Optional, if Docker Desktop is running:

```bash
docker build -t milaan-demo .
docker run --rm -p 7860:7860 milaan-demo
```

Open http://localhost:7860.

---

## 1. Push the code to GitHub

On your machine, inside the project folder:

```bash
git remote add origin https://github.com/saitejacodes/milaaan.git
git push -u origin phase-1-audit phase-2-improvements phase-3-web-demo
```

If `origin` already exists, skip the first line. Then open a pull request for
each branch, or merge `phase-3-web-demo` (it contains phases 1 and 2) into `main`:

```bash
git checkout main
git merge --ff-only phase-3-web-demo
git push origin main
```

If `main` has commits the branches don't, use `git merge phase-3-web-demo`
instead of `--ff-only`.

## 2. Create the Space

1. Sign up at https://huggingface.co/join (free).
2. Go to https://huggingface.co/new-space.
3. **Space name:** `milaan`. **License:** MIT. **SDK:** Docker, then **Blank**.
   **Hardware:** CPU basic (free). **Visibility:** Public.
4. Click **Create Space**. Leave the page open.

## 3. Create an access token

1. https://huggingface.co/settings/tokens, then **Create new token**.
2. Type **Write**. Name it `milaan-deploy`. Copy it. **Never commit it or paste it
   into the README, a screenshot or a video.**

## 4. Deploy

From the project folder, on the commit you want live:

```bash
scripts/deploy_hf.sh <your-hf-username> milaan
```

When git asks: **Username** = your Hugging Face username; **Password** = the token.

The script pushes a clean copy of `HEAD` with the Space's README (which holds the
`sdk: docker` and `app_port: 7860` settings). It leaves out `data/`, `docs/` and
images, because Hugging Face rejects binary files pushed without Xet/LFS and
the demo doesn't need them.

Watch the build at `https://huggingface.co/spaces/<you>/milaan?logs=build`. The
first build takes about 3–5 minutes. When the status turns **Running**, open the
App tab.

## 5. Check it (60 seconds)

- [ ] Overview shows **False matches 0** and **Adversarial attacks blocked 64/64**.
- [ ] **Run reconciliation** with 200 orders / hard returns **Gate: PASS**.
- [ ] **Exceptions**: clicking a row shows money at stake and next action.
- [ ] **Ask the agent**: "Why is cash blocked?" shows 3 steps.
- [ ] "Delete all exceptions." is refused and says *unchanged*.
- [ ] **Upload**: download the three sample CSVs, upload them back, and reconcile.

## 6. Put the link everywhere

- GitHub repo, **About** (⚙️): Website = your Space URL.
- README top (Phase 4 adds the badge).
- Resume and LinkedIn.

## 7. Optional: auto-deploy on every push to main

`.github/workflows/deploy-space.yml` redeploys after each push to `main`. It
does nothing until you add, in GitHub, **Settings → Secrets and variables →
Actions**:

- **Secret** `HF_TOKEN`: the write token from step 3.
- **Variable** `HF_SPACE`: for example `saitejacodes/milaan` (your HF username/space).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Build log: `pip` resolution error | Make sure you deployed a commit with `pillow==11.3.0` in `pyproject.toml` |
| Space stuck on "Starting" | Check that the Space README front matter says `app_port: 7860` |
| Push rejected: binary files | Deploy with `scripts/deploy_hf.sh`, which strips images |
| `Authentication failed` | Use a **Write** token as the password, not your account password |
| Page blank after the laptop slept | Reload the page; Streamlit reconnects |
| First visit slow | Normal after the Space slept: the container restarts, then about 3 s of live computation |

## Safety notes

- The demo never reads `.env` and never calls a model; the agent runs in offline mode.
- Each visitor gets a private temporary copy of the run database; questions
  append audit rows only to that copy.
- Uploads are limited to 5 MB per file and 25,000 rows, and are never stored
  beyond the container's temporary directory.
