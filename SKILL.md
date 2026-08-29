# [Project Name] Development Skill

## Project Context

[2-3 sentences about what the project does]

## Key Commands

- `uv sync` - Install dependencies
- `uv run pytest` - Run tests
- `uv run pre-commit run --all-files` - Run linters
- `uv add <package>` - Add dependency
- `uv add --dev <package>` - Add dev dependency

## Project Structure

```
main.py  # Main code
```

## Development Workflow

1. Make changes
2. Run pre-commit hooks automatically on commit
3. Tests run via uv run python script
4. Modify github actions
5. Update README.md to remove non-relevance.

## Important Notes

- Always support a CLI interface
- Use CDC, FDA and USDA FSIS government resources from .gov domains
- Use uv best practices

## Configuration Files

Refer to pyproject.toml and .pre-commit-config.yaml in this directory for exact setup.
