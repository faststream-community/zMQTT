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

The broker integration tests use the root `docker-compose.yaml`. Start all five
brokers before pytest:

```bash
docker compose up --detach --wait artemis mosquitto hivemq nanomq emqx
uv run pytest
```

To run only enhanced authentication tests:

```bash
uv run pytest -m emqx_auth -n 0
uv run pytest -m emqx_auth
```

EMQX uses port 1888 for ordinary connections (authentication disabled on that
listener) and port 1889 for SCRAM-SHA-256. The authenticator applies only to
`tcp:scram`. Docker Compose configures the authenticator. An integration-test
fixture checks the configuration and creates a separate user for each pytest
worker, removing it at session teardown. It uses the disposable API key in
`docker/emqx-api-keys.txt` on port 18083. These credentials are only for the
local test broker.

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
