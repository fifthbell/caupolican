# Contributing to Caupolicán

Thank you for your interest in contributing to Caupolicán! This document provides guidelines and instructions for contributing to the project.

## Conventional Commits

This project follows the [Conventional Commits](https://www.conventionalcommits.org/) specification for all commit messages and pull request titles. This ensures a consistent commit history and enables automated changelog generation.

### Commit Message Format

Each commit message must follow this format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

- **type**: The type of change (see below)
- **scope**: Optional - the area of the codebase affected (e.g., `api`, `worker`, `hls`, `docker`)
- **subject**: A brief description of the change (imperative mood, lowercase, no period at end)
- **body**: Optional - detailed explanation of the change
- **footer**: Optional - references to issues, breaking changes, etc.

### Commit Types

Use one of the following types:

- **feat**: A new feature
- **fix**: A bug fix
- **docs**: Documentation only changes
- **style**: Changes that don't affect code meaning (formatting, whitespace, etc.)
- **refactor**: Code change that neither fixes a bug nor adds a feature
- **perf**: Performance improvement
- **test**: Adding or updating tests
- **build**: Changes to build system or dependencies
- **ci**: Changes to CI configuration files and scripts
- **chore**: Other changes that don't modify src or test files
- **revert**: Reverts a previous commit

### Examples

#### Good commit messages:
```
feat(api): add endpoint for channel status retrieval

fix(worker): prevent ffmpeg crash on invalid stream

docs: update deployment instructions in README

chore: upgrade FastAPI to version 0.110.0

ci: add PR title validation workflow
```

#### Bad commit messages:
```
Update files
Fixed bug
WIP
Adding stuff
```

### Pull Request Titles

Pull request titles must also follow the conventional commit format, as they will become the commit message when using squash merge:

```
feat(hls): implement segment cleanup strategy
```

## Enforcement

Conventional commits are enforced through:

1. **Husky commit-msg hook**: Validates commit messages locally before they are created
2. **GitHub Actions**: Validates PR titles automatically
3. **Branch protection**: May require passing PR title validation before merging

### Local Setup

After cloning the repository, install dependencies:

```bash
npm install
```

This will set up Husky hooks automatically. Now when you commit, your commit message will be validated:

```bash
git commit -m "feat: add new feature"  # ✅ Valid
git commit -m "Adding new feature"     # ❌ Invalid - will be rejected
```

### Bypassing Validation (Not Recommended)

In rare cases where you need to bypass the commit message validation (e.g., merge commits), you can use:

```bash
git commit --no-verify -m "your message"
```

**Note**: This should only be used in exceptional circumstances.

## Development Workflow

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Ensure your commits follow the conventional commit format
5. Push to your fork
6. Open a Pull Request with a title following conventional commit format
7. Wait for CI checks to pass
8. Address review feedback if any

## Running the Application

See the main [README.md](README.md) for instructions on building and running the application.

## Questions?

If you have questions about contributing, feel free to open an issue for discussion.
