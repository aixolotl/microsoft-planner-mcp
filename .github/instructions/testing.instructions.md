---
applyTo: "tests/**"
description: "Use when writing or modifying tests. Covers pytest-asyncio strict mode, parameterized test conventions, mock patterns, and test file structure for this project."
---
# Testing Conventions

## Framework

- **pytest** with **pytest-asyncio** in `strict` mode — every async test needs `@pytest.mark.asyncio`
- Run tests: `uv run pytest -v`

## Parameterized Tests

Always use `@pytest.mark.parametrize` with the `ids` keyword for readable test output. Place the decorator directly above the test function (after `@pytest.mark.asyncio` if async).

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [412, 409], ids=["conflict-412", "conflict-409"])
async def test_retries_on_conflict(status):
    ...

@pytest.mark.parametrize("input_val,expected", [
    ("a,b,c", ["a", "b", "c"]),
    ("x", ["x"]),
], ids=["multi-csv", "single-value"])
def test_parse_csv(input_val, expected):
    ...
```

Rules:
- **Always provide `ids=`** with short, descriptive kebab-case labels
- One test ID per parameter tuple — count of IDs must match count of parameter tuples
- Group related scenarios under a single parameterized test when they share identical assertion logic
- Use separate test functions when scenarios need different setup or assertions

## Test File Structure

1. **Module docstring** — one-line summary of what is tested
2. **Imports** — standard lib, third-party, then project (`from src.…`)
3. **Helpers section** — factory functions prefixed `make_` (e.g. `make_task()`, `make_graph_client()`)
4. **Test sections** — grouped by concern with comment separators:

```python
# ---------------------------------------------------------------------------
# Tests: authorisation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: return values
# ---------------------------------------------------------------------------
```

## Mock Patterns

- `AsyncMock` for async methods, `MagicMock` for sync objects
- Use `@asynccontextmanager` + `yield` to mock `graph_client_manager.for_user()`
- Patch at the import location: `patch("src.tools.plans.get_access_token", ...)`
- Use `make_*` factory helpers to construct test doubles — keep tests declarative

```python
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

@asynccontextmanager
async def _for_user(token):
    yield graph_client

with patch("src.tools.module.get_access_token", return_value=make_access_token()), \
     patch("src.tools.module.graph_client_manager") as mock_mgr:
    mock_mgr.for_user = _for_user
    result = await tool_function()
```

## Naming

- Test files: `test_<module_name>.py`
- Test functions: `test_<behaviour_under_test>` — describe the expected outcome, not the method name
- Helper factories: `make_<thing>()` with sensible defaults
