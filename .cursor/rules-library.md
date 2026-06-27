# 📘 Cursor Rules — Python Library / Django Library Template

These rules apply when this repository is used as a **Python library**  
(e.g., utilities, reusable Django apps, shared modules).

Cursor/Copilot must follow these conventions.

---

# 1. Goals

A high-quality Python/Django library with:

- `src/` layout
- Clear public API
- Strict typing
- Semantic versioning
- Multi-version CI matrix
- Minimal dependencies
- High coverage testing

---

# 2. Tools & Runtime

- Python **3.14** (may support multiple versions)
- `uv` for dependency management
- `ruff` for lint/format
  - Automatically removes unused imports (F401)
  - Automatically sorts imports (I rules)
- `mypy` strict
- `pytest`
- Snapshot testing
- Hypothesis

**All configuration must live in `pyproject.toml`.**

**Pre-commit hooks automatically enforce:**

### Lint (ruff)
- Removes unused imports (F401)
- Sorts imports according to ruff/isort rules (I rules)
- Checks for code quality issues (E, F, I, W rules)
- Auto-fixes issues with `--fix` flag
- Fails commit if fixes are needed (`--exit-non-zero-on-fix`)

### Format (ruff-format)
- Formats code according to project style
- Enforces consistent code formatting

### Type Annotation (mypy)
- Performs strict type checking (`--strict` mode)
- Validates type annotations on all `src/` files
- Ensures type safety across the codebase

---

# 3. Project Structure (Library Mode)

```
project/
  src/
    package_name/
      __init__.py

  tests/
    unit/
    integration/

  pyproject.toml
  Makefile
  .pre-commit-config.yaml
```

---

# 4. Library Coding Standards

- Must define explicit public API
- Use `__all__` or root modules for API exports
- No heavy logic in `__init__`
- Fully typed
- Keep dependencies minimal
- Follow semantic versioning (semver)
- No side effects at import time
- **Imports must be sorted and unused imports removed** (enforced by pre-commit)

---

# 5. Django-Specific Rules

If this library is Django-related:

- Provide an `AppConfig`
- Use `AppConfig.ready()` for initialization
- Provide integration docs:
  - INSTALLED_APPS
  - settings
  - URLs
  - signals
  - middleware
- No DB/network calls at import time

---

# 6. Testing Requirements

### Unit Tests
- Required for all logic
- Coverage ≥ 85%
- Snapshot + Hypothesis allowed

### Integration Tests
- Use minimal Django test project if needed

### CI Matrix
```
python: [3.12, 3.13, 3.14]
django: [4.2, 5.0]       # if Django library
```

---

# 7. Makefile (Library Mode)

### Setup
```
make init
make clean
```

### Validation
```
make fix    # Fix linting + format (removes unused imports, sorts imports)
make check  # Run all checks (format-check, lint, typecheck)
```

### Tests
```
make test-unit
make test-int
make test-all
```

### Packaging
```
make build
make publish
```

---

# 8. Documentation Rules

Library must include:

- README with quickstart
- Installation instructions
- Example usage
- Public API description
- CHANGELOG for releases
- Type-hinted docstrings

---

# 9. Cursor/Copilot Behavior Rules

- Use src/ layout
- Maintain a clean public API
- Avoid coupling to specific apps/projects
- Use strict typing everywhere
- Generate matching tests
- Respect backward compatibility
- Use Makefile in examples
- Provide docstrings + examples
- **Always remove unused imports and sort imports** (pre-commit will enforce this)

---

# ✔ End of Library Rules
