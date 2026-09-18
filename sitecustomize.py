# Imported by the interpreter at startup, before pytest, coverage, or anything
# else. That timing is the whole point.
#
# key_value.aio -- a FastMCP dependency, used for OAuth client storage --
# installs a beartype import hook the moment it is imported. If coverage is
# already running when that happens, beartype's own modules end up importing
# each other through that hook and fail on a circular import, taking the whole
# suite with it before a single test runs.
#
# Importing it here means the hook is installed while nothing else is
# instrumenting imports, so the cycle never forms.
try:
    import key_value.aio  # noqa: F401
except Exception:  # pragma: no cover - absent in a minimal install
    pass
