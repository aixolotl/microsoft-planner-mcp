---
applyTo: "tests/**"
description: "Use when writing or modifying tests. Covers pytest-asyncio auto mode, parameterized test conventions, mock patterns, and test file structure for this project."
---
# Testing Conventions

## Commenting Convention

Apply the same three-part comment pattern as the source code:
1. **Why** — the reason the test or setup exists
2. **What breaks without it** — what would go undetected
3. **Docs link** — where to read more

Apply this to:
- The `MODULE` constant (why it must be the import/usage site, not the definition site)
- Non-obvious mock shapes (e.g. why `result.value = None` AND `result = None` are both tested)
- Fixture choice explanations (`graph_ctx` vs `token_capturing_ctx`)
- Parametrize IDs that are not self-explanatory

```python
# Patch at the usage module, not the definition module. Python resolves the
# imported reference when the tool module is first loaded; patching the
# definition site after that has no effect on the already-bound name.
MODULE = "src.tools.tasks"
```


## Framework

- **pytest** with **pytest-asyncio** in `auto` mode — async tests are detected automatically, no `@pytest.mark.asyncio` needed
- Run tests: `uv run pytest -v`

## Parameterized Tests

Always use `@pytest.mark.parametrize` with the `ids` keyword for readable test output. Place the decorator directly above the test function (after `@pytest.mark.asyncio` if async).

```python
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
- Patch at the import location: `patch("src.tools.plans.get_access_token", ...)`
- Use `make_*` factory helpers to construct test doubles — keep tests declarative
- Use the `graph_ctx` fixture (from `conftest.py`) to patch both `get_access_token` and `graph_client_manager.for_user` in one step
- Use the `token_capturing_ctx` fixture when you need to assert which OBO token was forwarded

```python
# Standard tool test — graph_ctx patches auth + client in one context manager
async def test_returns_result(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1")

    assert result == tasks


# OBO token forwarding — token_capturing_ctx records the token passed to for_user
async def test_obo_token_forwarded(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.get = AsyncMock(return_value=make_tasks_result([]))

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_tasks("plan-1")

    assert received == ["my-obo"]
```

## Naming

- Test files: `test_<module_name>.py`
- Test functions: `test_<behaviour_under_test>` — describe the expected outcome, not the method name
- Helper factories: `make_<thing>()` with sensible defaults
