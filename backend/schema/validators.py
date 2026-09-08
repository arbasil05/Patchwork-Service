"""Shared validation constants for package name sanitization."""
import re

# Regex for NPM package validation:
# - Can optionally start with an @scope/
# - Name can contain lowercase letters, numbers, hyphens, underscores, dots.
# - No other slashes, no spaces.
NPM_NAME_REGEX = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")

# Regex for PyPI package validation (PEP 503):
# - ASCII letters, numbers, '.', '-', and '_'
PYPI_NAME_REGEX = re.compile(r"^[a-zA-Z0-9._-]+$")
