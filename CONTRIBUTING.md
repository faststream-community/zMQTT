# Contributing to zmqtt

Thank you for contributing. See faststream-community [code of conduct](https://github.com/faststream-community/.github/blob/main/CODE_OF_CONDUCT.md)

## Development

Install the locked development environment:

```bash
uv sync --locked --group dev
```

Run the same core checks as CI:

```bash
uv run ruff check
uv run ruff format
uv run mypy
uv run pytest
uv run mkdocs build --strict
uv build
```

The broker integration tests expect the brokers from `docker/docker-compose.yaml`.

## Pull requests

Keep each pull request focused and use a
[Conventional Commits](https://www.conventionalcommits.org/) title. Pull
requests are squash-merged, so the title becomes the release commit:

- `feat(client): add reconnect timeout` produces a minor release.
- `fix(codec): handle empty properties` produces a patch release.
- `feat!: remove deprecated API` marks a breaking change.
- `docs: clarify TLS setup` does not produce a release by itself.

All required checks must pass again in GitHub's merge queue before the pull
request is merged.
