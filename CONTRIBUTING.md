# Contributing to Django Observe Kit

Thank you for your interest in contributing to Django Observe Kit! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Environment](#development-environment)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Commit Messages](#commit-messages)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Django-Observe_Kit.git
   cd Django-Observe_Kit
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/wiphoo/Django-Observe_Kit.git
   ```

## Development Environment

### Prerequisites

- **Python 3.10+** (we support 3.10, 3.11, 3.12)
- **uv** - Fast Python package manager ([install guide](https://github.com/astral-sh/uv))
- **Docker** and **Docker Compose** - For integration tests
- **Make** - For running development commands

### Setup

```bash
# Initialize the project (installs dependencies + pre-commit hooks)
make init

# Verify everything works
make check
make test-unit
```

### Available Make Commands

Run `make help` for a full list. Key commands:

| Command | Description |
|---------|-------------|
| `make init` | First-time setup |
| `make dev` | Quick dev loop (fix + test-unit) |
| `make pr` | Pre-PR checks (check + test-unit) |
| `make check` | Run all quality checks |
| `make test` | Run the default unit suite |
| `make test-unit` | Run unit tests only |
| `make integration-up` | Start Docker services |
| `make integration-down` | Stop Docker services |

## Code Style

We use **ruff** for linting and formatting, and **mypy** for type checking.

### Formatting

```bash
# Auto-fix and format
make fix

# Check without modifying
make check
```

### Type Hints

All public functions must have type annotations:

```python
# Good
def process_request(request: HttpRequest, timeout: int = 30) -> HttpResponse:
    ...

# Bad - missing types
def process_request(request, timeout=30):
    ...
```

### Import Order

Imports are sorted automatically by ruff. The order is:
1. Standard library
2. Third-party packages
3. Local imports (`observe_kit.*`)

### Docstrings

Use Google-style docstrings for public APIs:

```python
def sanitize_headers(headers: dict[str, str], pii_level: str = "BASIC") -> dict[str, str]:
    """Sanitize HTTP headers based on PII level.

    Args:
        headers: Dictionary of header names to values.
        pii_level: One of "NONE", "BASIC", or "SENSITIVE".

    Returns:
        Sanitized headers dictionary.

    Raises:
        ValueError: If pii_level is not recognized.
    """
```

## Testing

### Test Structure

```
tests/
├── unit/           # Fast, isolated tests (no external dependencies)
├── integration/    # Tests requiring Docker services
└── e2e/            # End-to-end workflow tests
```

### Running Tests

```bash
# Run the default unit suite
make test

# Run only unit tests (fast)
make test-unit

# Run integration tests (requires Docker)
make integration-up
make test-int
make integration-down

# Run with coverage
make test-cov
```

### Writing Tests

- Use **pytest** (not unittest.TestCase)
- Use **pytest.mark.parametrize** for multiple test cases
- Use **hypothesis** for property-based testing when appropriate
- Integration tests must be marked with `@pytest.mark.integration`

Example:

```python
import pytest

@pytest.mark.parametrize("pii_level,expected", [
    ("NONE", "user@example.com"),
    ("BASIC", "u***@example.com"),
    ("SENSITIVE", "[REDACTED]"),
])
def test_email_sanitization(pii_level: str, expected: str) -> None:
    result = sanitize_email("user@example.com", pii_level)
    assert result == expected
```

### Coverage Requirements

- Minimum coverage: **90%**
- New code should have tests
- Check coverage with `make test-cov-html`

## Pull Request Process

### Before Submitting

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** and commit them

3. **Run pre-PR checks**:
   ```bash
   make pr
   ```

4. **Update documentation** if needed

5. **Add a CHANGELOG entry** for user-facing changes

### Submitting

1. Push your branch to your fork
2. Open a Pull Request against `main`
3. Fill out the PR template
4. Wait for CI to pass
5. Address review feedback

### PR Checklist

- [ ] Default tests pass (`make test`)
- [ ] Full suite passes when relevant (`make test-all`)
- [ ] Code quality checks pass (`make check`)
- [ ] Documentation updated (if applicable)
- [ ] CHANGELOG.md updated (for user-facing changes)
- [ ] Commit messages follow conventions

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Formatting, no code change
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `perf`: Performance improvement
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Examples

```
feat(otel): add custom span attribute support

fix(pii): handle None values in header sanitization

docs: update README with new middleware order

test(audit): add integration tests for audit logging
```

## Reporting Issues

### Bug Reports

When reporting bugs, please include:

1. **Python version** (`python --version`)
2. **Django version** (`python -c "import django; print(django.VERSION)"`)
3. **observe_kit version**
4. **Steps to reproduce**
5. **Expected behavior**
6. **Actual behavior**
7. **Error messages/stack traces**

### Feature Requests

For feature requests, please describe:

1. **The problem** you're trying to solve
2. **Your proposed solution**
3. **Alternatives** you've considered
4. **Additional context**

## Questions?

- Open a [GitHub Discussion](https://github.com/wiphoo/Django-Observe_Kit/discussions)
- Check existing issues and discussions first

Thank you for contributing!
