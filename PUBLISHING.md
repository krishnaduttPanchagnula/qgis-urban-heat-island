# Publishing `uhi-desktop` to PyPI

The package name **`uhi-desktop` is available** (checked on pypi.org). The build is
already verified locally: `python -m build` produces a wheel and sdist, and
`twine check dist/*` passes.

There are two ways to publish. **Trusted publishing (option A) is recommended** —
no API token is stored anywhere.

---

## Before you start (both methods)

From a checkout of this repo:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

You should see two files in `dist/` and `PASSED` for both.

> Note: GDAL is intentionally **not** a PyPI dependency — there is no portable
> wheel. Users install it with conda or their OS package manager (documented in
> `desktop_app/README.md`). The wheel itself is pure Python and installs anywhere.

---

## Option A — Trusted publishing from GitHub (recommended)

### 1. Configure PyPI

1. Sign in to https://pypi.org and enable two-factor authentication.
2. Go to **Account settings → Publishing** (for a brand-new project, use
   **Add a pending publisher**; for an existing project, use the project's
   **Settings → Publishing → Add a GitHub publisher**).
3. Fill in:
   - **PyPI Project Name:** `uhi-desktop`
   - **Owner:** `krishnaduttPanchagnula`
   - **Repository name:** `qgis-urban-heat-island`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`

The workflow file (`.github/workflows/publish.yml`) is already in the repo.

### 2. Configure GitHub

1. Repo **Settings → Environments → New environment**, name it exactly `pypi`.
   Optionally add required reviewers so publishing needs your approval.
2. Push a commit containing the workflow (already done on `main`).

### 3. Publish

Either:

- **Actions → Publish to PyPI → Run workflow** (manual), or
- publish a GitHub release — the workflow runs automatically on `release: published`.

The job builds the distributions in one job and uploads them with OIDC in a
second job that has `id-token: write`. No secrets are needed.

### 4. Verify

```bash
pip install uhi-desktop
uhi-desktop          # launches the GUI
```

Check https://pypi.org/project/uhi-desktop/.

---

## Option B — Manual upload with an API token

### 1. Create a token

1. On PyPI: **Account settings → API tokens → Add API token**.
2. Scope it to the project `uhi-desktop` (create the project via the first upload
   if it does not exist yet, or use a pending publisher from option A).
3. Copy the token — it starts with `pypi-`.

### 2. Test on TestPyPI first (optional but wise)

Create a separate token at https://test.pypi.org/manage/account/token/, then:

```bash
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ uhi-desktop
```

### 3. Upload to PyPI

```bash
python -m twine upload dist/*
```

When prompted: username `__token__`, password your `pypi-...` token.
Or non-interactively:

```bash
TWINE_USERNAME=__token__ TWINE_PASSWORD='pypi-...' python -m twine upload dist/*
```

### 4. Verify

Same as option A: `pip install uhi-desktop`, run `uhi-desktop`, check the PyPI page.

---

## Releasing a new version

1. Update the version in **all** places:
   - `pyproject.toml` → `version`
   - `uhi_landsat/metadata.txt` → `version`
   - `plugins.xml` → `version`, `file_name`, `download_link`
   - `CHANGELOG.md`
2. Rebuild: `rm -rf dist && python -m build && python -m twine check dist/*`.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Publish the GitHub release, which triggers the PyPI workflow.
   PyPI does not allow re-uploading an existing version — bump the number every time.
