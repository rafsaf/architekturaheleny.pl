# architekturaheleny.pl

Static portfolio site generated with Python + Jinja templates.

## CMS token

Copy `.env.example` to `.env` and set:

```bash
CMS_TOKEN=your_directus_static_token_here
```

For GitHub Actions deployment, add the same token as repository secret named `CMS_TOKEN`.

## CMS webhook trigger

Besides release-based builds, deploy can also be triggered from CMS using GitHub API `workflow_dispatch`.

For a fine-grained PAT, grant access to this repository and set repository permission:

- `Actions: Write`

Example request:

```bash
curl -X POST \
	-H "Accept: application/vnd.github+json" \
	-H "Authorization: Bearer <GITHUB_TOKEN_WITH_ACTIONS_WRITE>" \
	https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/gh-pages.yml/dispatches \
	-d '{"ref":"main"}'
```

You can also run it manually in GitHub Actions via `workflow_dispatch` in the UI.

## Local build

```bash
uv sync --locked --all-extras --dev
uv run python scripts/download_from_cms.py
uv run python template.py
```

Generated static files are written to `out/`.
