# Project Workflow Rules

1. **Task flow:** every task follows these steps in order:
   design → AI review of design → plan → AI review of plan → write tests → run tests, confirm they fail → implement → run tests, confirm they pass → AI code review.
   - After each AI review, the user reviews too. Wait for their sign-off before moving to the next step.
   - If a review finds problems: fix → re-run tests → review again, until clean.
   - Small changes (typos, one-line fixes): skip design and plan; keep tests, AI review and user sign-off.
   - Any code update outside a planned task still gets an AI review, then the user's review.
2. **Fresh reviewer:** AI reviews are done by a separate reviewer agent that sees only the artifact (design, plan or diff), not the author's reasoning.
3. **Where docs go:** save designs to `docs/superpowers/specs/` and plans to `docs/superpowers/plans/`, named `YYYY-MM-DD-<topic>.md`.
4. **Code quality:** code must always be clean, concise and follow best practice.
   - Match the surrounding code's style, naming and structure.
   - No dead code, unused imports, commented-out code, debug prints or duplicated logic.
   - Keep functions small and single-purpose; prefer clear names over comments.
   - Use the framework's idiomatic approach (FastAPI, Vue 3 Composition API, TypeScript strict types).
   - Lint and type-check before handing off.
5. **Concise comments:** keep code comments short, ideally under 20 words.
6. **No git writes:** never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. The user does these. This overrides any skill that asks for them.
7. **Suggested commit:** after every file change, end the reply with a suggested commit message under 10 words.