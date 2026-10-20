# Contributing to GLOW

Thank you for your interest in contributing to GLOW!

## Development Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests (see below)
5. Commit your changes (see commit guidelines)
6. Push to your fork: `git push origin feature/my-feature`
7. Open a Pull Request

## Testing Requirements

### API Changes

```bash
cd api
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

### Dashboard Changes

```bash
cd dashboard
npm run test
npm run check
npx tsc
npm run build
```

## Commit Guidelines

We use semantic commit messages:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting, no logic change)
- `refactor:` Code refactoring
- `test:` Test changes
- `chore:` Build process or tooling changes

**Examples:**
```
feat(api): add endpoint for data export
fix(dashboard): correct chart rendering issue
docs: update deployment instructions
refactor(api): simplify database query logic
```

### Detailed Commit Messages

For important changes, include:
- **What** changed (summary line)
- **Why** it changed (motivation)
- **How** it changed (if not obvious)
- Any relevant issue numbers

**Example:**
```
fix(api): use stable key when merging export columns

The merge used a computed value as the key, which is not known until
runtime and caused spurious KeyErrors.

Switched to the configured column name, which is deterministic.

Fixes: export failure on fresh datasets
Related: #123
```

## Code Style

### Python (API)

- Follow PEP 8
- Use Ruff for linting and formatting
- Type hints required for public functions
- Docstrings for modules, classes, and public functions

### TypeScript (Dashboard)

- Follow the project's ESLint configuration
- Use TypeScript strict mode
- Prefer functional components
- Accessible selectors in tests

## Pull Request Process

1. Update documentation if you change functionality
2. Ensure all tests pass (CI will check)
3. Request review from maintainers
4. Address review feedback
5. Squash commits if requested (we prefer clean history)

## Questions?

Open an issue or reach out to the maintainers.
