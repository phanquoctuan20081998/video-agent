# Contributing Guide

Thank you for your interest in contributing to Video Agent! 🎬

## Code of Conduct

Please be respectful and constructive in all interactions.

## How to Contribute

### 1. Report Issues
- Check if issue already exists
- Provide clear description
- Include error messages and logs
- List steps to reproduce

### 2. Submit Pull Requests

**Process:**
1. Fork the repository
2. Create feature branch: `git checkout -b feature/my-feature`
3. Make changes following code style
4. Add tests if applicable
5. Update documentation
6. Commit: `git commit -am 'Add feature: description'`
7. Push: `git push origin feature/my-feature`
8. Create Pull Request

**PR Requirements:**
- Clear title and description
- Link to related issues
- Tests pass
- Documentation updated
- Code follows style guide

### 3. Code Style

**Python:**
```python
# Use black formatter
black src/ tests/

# Run linter
ruff check src/

# Type hints preferred
async def fetch_videos(query: str) -> List[StockVideo]:
    pass
```

**Async Code:**
- Use `async`/`await` for I/O operations
- Use `asyncio.gather()` for concurrent operations
- Proper error handling with try/except

**Documentation:**
- Docstrings for all functions/classes
- Type hints in signatures
- Examples in docstrings

### 4. Testing

```bash
# Run tests
pytest tests/

# With coverage
pytest --cov=src tests/

# Specific test
pytest tests/test_agent.py::TestLLM::test_llm_chat
```

**Test Requirements:**
- Unit tests for new functions
- Mock external APIs
- Use fixtures for common data
- Minimum 70% coverage

### 5. Documentation

**Update documentation for:**
- New features
- API changes
- Configuration options
- Error codes

**Files to update:**
- README.md - User guide
- ARCHITECTURE.md - Technical design
- API_KEYS_SETUP.md - API configuration
- TROUBLESHOOTING.md - Known issues

## Development Setup

```bash
# Clone repo
git clone https://github.com/yourusername/video-agent.git
cd video-agent

# Setup development environment
chmod +x setup.sh && ./setup.sh

# Install dev dependencies
pip install -r requirements.txt[dev]

# Pre-commit hooks (recommended)
pip install pre-commit
pre-commit install
```

## Areas for Contribution

### High Priority
- [ ] Web UI (Streamlit)
- [ ] Multi-platform support
- [ ] Performance optimization
- [ ] Bug fixes

### Medium Priority
- [ ] Additional stock video sources
- [ ] New LLM providers
- [ ] Advanced video effects
- [ ] Analytics

### Low Priority
- [ ] Documentation improvements
- [ ] Examples
- [ ] Code cleanup
- [ ] Tests

## Commit Messages

```
type(scope): description

# Types: feat, fix, docs, style, refactor, test, chore
# Example:
feat(video-editor): add video resizing support
fix(llm): handle timeout errors gracefully
docs(readme): add quick start guide
```

## Release Process

1. Update CHANGELOG.md
2. Update version in `__init__.py`
3. Create git tag: `git tag v0.2.0`
4. Push tag: `git push origin v0.2.0`
5. Create GitHub Release

## Questions?

- GitHub Issues for bugs/features
- GitHub Discussions for questions
- Documentation at README.md and ARCHITECTURE.md

Thank you for contributing! ❤️
