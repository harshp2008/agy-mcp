# NOTES

## Findings
- Project contains `slug/__init__.py` implementing `slugify(text)`.
- `slugify` converts text by lowercasing and replacing spaces with hyphens (`text.lower().replace(" ", "-")`).
- `README.md` currently contains only a single description line and lacks a usage examples section.
- `tests/test_slug.py` verifies basic functionality (`assert slugify("Hello World") == "hello-world"`).

## Decisions
- Update `slugify` docstring in `slug/__init__.py` to clearly describe behavior.
- Add `## Usage` section with two examples to `README.md`.
