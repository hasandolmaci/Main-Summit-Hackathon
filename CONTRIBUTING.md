# How we work (hackathon edition)

Speed matters, but so does not breaking each other's code. Keep it simple:

## 1. Always start from a fresh `main`

```bash
git checkout main
git pull
```

## 2. Make a branch for your task

```bash
git checkout -b feature/short-description   # e.g. feature/login-page, fix/api-crash
```

Prefixes: `feature/`, `fix/`, `docs/`, `chore/`.

## 3. Commit small and often

```bash
git add .
git commit -m "Add login form"
git push -u origin feature/short-description
```

## 4. Open a Pull Request into `main`

- Use the PR template, link the issue (`Closes #12`).
- Ask one teammate to glance at it. During crunch time, self-merge is fine if it runs.
- Prefer **Squash and merge** to keep history clean.

## 5. Resolve conflicts early

If your branch is behind, update it:

```bash
git checkout main && git pull
git checkout feature/short-description
git merge main
```

## Rules

- **Never commit secrets.** Put keys in `.env` (ignored). Add the variable *name* to `.env.example`.
- `main` should always run — it's what we demo.
- Track tasks as GitHub Issues (or the Project board) and assign yourself so nobody duplicates work.
- Two people editing the same file at once = conflicts. Coordinate in chat first.
