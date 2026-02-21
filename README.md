# architekturaheleny.pl

Static portfolio site generated with Python + Jinja templates.

## CMS token

Copy `.env.example` to `.env` and set:

```bash
CMS_TOKEN=your_directus_static_token_here
```

For GitHub Actions deployment, add the same token as repository secret named `CMS_TOKEN`.

## CMS webhook trigger

Besides release-based builds, deploy can also be triggered from CMS using GitHub API `repository_dispatch` (event type: `cms_publish`).

Example request:

```bash
curl -X POST \
	-H "Accept: application/vnd.github+json" \
	-H "Authorization: Bearer <GITHUB_TOKEN_WITH_REPO_SCOPE>" \
	https://api.github.com/repos/<OWNER>/<REPO>/dispatches \
	-d '{"event_type":"cms_publish"}'
```

You can also run it manually in GitHub Actions via `workflow_dispatch`.

## Local build

```bash
uv sync --locked --all-extras --dev
uv run python scripts/download_from_cms.py
uv run python template.py
```

Generated static files are written to `out/`.
