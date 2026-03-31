# Testing Guide

## Overview

The observe_kit project includes comprehensive unittest-based tests targeting **85% code coverage** using `pytest.mark.parametrize` for data-driven testing.

## Test Structure

Tests are organized using:
- **unittest.TestCase** as base classes
- **pytest.mark.parametrize** for test tables/data-driven tests
- **unittest.mock** for mocking dependencies

## Running Tests

### Basic Test Execution

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_context_comprehensive.py

# Run specific test class
pytest tests/test_context_comprehensive.py::TestRequestContext

# Run specific test method
pytest tests/test_context_comprehensive.py::TestRequestContext::test_request_context_initialization
```

### Coverage Reports

```bash
# Run with coverage (terminal report)
pytest --cov=observe_kit --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=observe_kit --cov-report=html

# View HTML report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux

# Run with coverage threshold (fails if < 85%)
pytest --cov=observe_kit --cov-fail-under=85
```

### Running Parametrized Tests

```bash
# Run all parametrized tests
pytest -k "parametrize" -v

# Run tests matching pattern
pytest -k "test_request_context" -v
```

## Test Files

### Comprehensive Test Suites

1. **test_context_comprehensive.py** - Context management, RequestContext, RequestTiming
2. **test_pii_rules_comprehensive.py** - PII sanitization, PiiConfig, PiiLevel
3. **test_middleware_comprehensive.py** - All middleware classes
4. **test_otel_comprehensive.py** - OTEL tracing, configuration, middleware
5. **test_metrics_comprehensive.py** - Metrics, QueryRecorder, Prometheus
6. **test_logging_comprehensive.py** - Logging configuration, filters, sanitization
7. **test_sentry_comprehensive.py** - Sentry configuration, event scrubbing
8. **test_audit_comprehensive.py** - Audit logging, AuditLog model
9. **test_tenant_comprehensive.py** - Tenant resolution
10. **test_health_comprehensive.py** - Health check endpoints
11. **test_drf_comprehensive.py** - DRF integration, exception handling

### Existing Test Files

- test_context.py
- test_pii_rules.py
- test_pii_config.py
- test_body_sanitization.py
- test_config_validation.py
- test_drf_integration.py
- test_trace_propagation.py
- test_metrics.py
- test_middleware_http.py
- test_auditlog.py
- test_wagtail_hooks.py

**Total**: 23 test files

## Test Patterns

### Example: Parametrized Test

```python
import unittest
import pytest

class TestComponent(unittest.TestCase):
    @pytest.mark.parametrize(
        "input1,input2,expected",
        [
            ("value1", "value2", "result1"),
            ("value3", "value4", "result2"),
        ],
    )
    def test_feature(self, input1, input2, expected):
        """Test description."""
        result = function_under_test(input1, input2)
        self.assertEqual(result, expected)
```

### Example: Mocking

```python
from unittest.mock import Mock, patch

class TestMiddleware(unittest.TestCase):
    def test_process_request(self):
        request = Mock()
        request.method = "GET"
        
        with patch("module.external_dependency") as mock_dep:
            mock_dep.return_value = "value"
            result = middleware.process_request(request)
            self.assertIsNotNone(result)
```

## Coverage Goals

- **Overall**: 85% minimum
- **Core Components**: 95%+
- **Middleware**: 90%+
- **Integration**: 85%+
- **Edge Cases**: 80%+

## Continuous Integration

Tests are configured to:
- Run automatically on commits
- Fail if coverage drops below 85%
- Generate HTML coverage reports
- Support parallel execution

## Adding New Tests

When adding new features:

1. **Create test file**: `tests/test_<feature>_comprehensive.py`
2. **Use unittest.TestCase**: Inherit from `unittest.TestCase`
3. **Add parametrize decorators**: Use `@pytest.mark.parametrize` for data-driven tests
4. **Mock dependencies**: Use `unittest.mock` for external dependencies
5. **Test edge cases**: Include boundary conditions and error cases
6. **Maintain coverage**: Ensure new code is covered

## Test Maintenance

- Keep tests in sync with code changes
- Update parametrize tables when adding new scenarios
- Review coverage reports regularly
- Refactor tests when code is refactored

## Troubleshooting

### Tests fail with import errors
```bash
# Install dev dependencies
pip install -e .[dev]
```

### Coverage not generating
```bash
# Install coverage tools
pip install pytest-cov coverage
```

### Tests timeout
```bash
# Run with timeout
pytest --timeout=30
```

---

For detailed coverage information, see `TEST_COVERAGE_SUMMARY.md`.



