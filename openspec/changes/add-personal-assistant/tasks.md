# Tasks

## 1. Personal data store foundation

- [ ] 1.1 Add gitignored `personal/` directory (with `.gitkeep`) mirroring `company/`, plus `.gitignore` entry, and verify `git status` shows nothing new under `personal/` after adding a test file there
- [ ] 1.2 Add `PERSONAL_PROFILE_PATH` / personal docs path settings to `config.py`, defaulting under `personal/`, and verify a unit test confirms the defaults and env-var overrides resolve to absolute paths
- [ ] 1.3 Implement `PersonalProfile` Pydantic model + `to_prompt_block()`-style renderer and loader (e.g. `memory/personal_profile.py`), separate from `CompanyProfile`, and verify a unit test loads a sample `personal/profile.yaml` and confirms a missing file degrades gracefully (empty/no profile, no exception)
- [ ] 1.4 Add a dedicated `PERSONAL_COLLECTION` constant and setup in `knowledge/store.py`, never referenced by any existing collection's query path, and verify a unit test confirms it is a distinct collection from `COMPANY_COLLECTION`/`BUILTIN_COLLECTION`
- [ ] 1.5 Add a `DOMAIN_ALIASES` entry (or equivalent routing) in `knowledge/retriever.py` so personal-domain retrieval queries only `PERSONAL_COLLECTION`, and verify a unit test confirms a personal-domain query never touches `COMPANY_COLLECTION`/`BUILTIN_COLLECTION`

## 2. Personal assistant specialist

- [ ] 2.1 Add `PERSONAL_ASSISTANT_PROMPT` to `prompts/domain_prompts.py` covering both supported domains (life-admin/scheduling; family/relationships), following the existing prompt shape (identity, expertise, rules of thumb, guidance steps)
- [ ] 2.2 Create `agents/personal_assistant.py` subclassing `BaseAgent` (`name="personal_assistant"`, `domain="personal_assistant"`), and verify a unit test instantiates it and confirms `get_system_prompt()` returns the new prompt constant
- [ ] 2.3 Register `personal_assistant` in `SPECIALIST_REGISTRY` and `SPECIALIST_DESCRIPTIONS` in `orchestrator/router.py`, and verify a unit test confirms the `consult_specialist` tool schema's `specialist` enum includes `"personal_assistant"`
- [ ] 2.4 Add a `personal_assistant` entry to `_AREAS` in `orchestrator/answer_sources.py`, and verify the existing "every registered specialist has an area" test passes with the new entry
- [ ] 2.5 Wire personal-profile injection into the specialist's own user-turn context only (collapsed line/tag, analogous to `company_stage`), and verify a unit test confirms `PersonalProfile` content appears in a `personal_assistant` call's context but not in any business specialist's `analyze()` call built in the same test

## 3. Isolation guarantees (spec: personal-data/store, orchestrator/personal-assistant)

- [ ] 3.1 Add an integration test that consults a business specialist (e.g. `cfo`) and `personal_assistant` in the same turn via `route_parallel`, and verify neither specialist's retrieved knowledge, profile, or context leaks into the other's inputs
- [ ] 3.2 Add a unit test confirming the Executive's `build_system_blocks()` cache blocks never contain personal profile content, regardless of whether a personal profile file exists
- [ ] 3.3 Add a unit test confirming a `personal_assistant` specialist failure (raised exception/timeout) is isolated the same way a business specialist failure is, and does not prevent the Executive's turn from completing with other specialists' results

## 4. Eval scenarios

- [ ] 4.1 Add `packages/core/openexecutive/evals/_scenarios/personal_assistant_001.yaml` (life-admin/scheduling scenario) with a `personal_context` field, `expected_tool_calls`, `expected_topics`, and `quality_criteria`, and verify `make eval` runs it without schema errors
- [ ] 4.2 Add `packages/core/openexecutive/evals/_scenarios/personal_assistant_002.yaml` (family/relationships scenario), and verify `make eval` runs it without schema errors

## 5. Architecture docs

- [ ] 5.1 Re-author `architecture/prebuilt/agents.json` to include the `personal_assistant` specialist (per CLAUDE.md: pure `SPECIALIST_REGISTRY` additions are usually auto-reflected — confirm via `python -m json.tool` validation and the existing arch-doc drift check whether a manual edit is actually needed)
- [ ] 5.2 Add the personal/company data isolation invariant to `architecture/architecture-facts.yaml`, and verify `scripts/pr_checks.py`'s arch-doc-drift check passes for the modules touched in this change

## 6. Final verification

- [ ] 6.1 Run `make check` (lint, mypy, unit tests, pr_checks) and verify it passes end to end
- [ ] 6.2 Manually exercise a personal-life query and a business query in the same conversation via `make dev`, and verify the Executive correctly routes each to the right specialist and the responses show no cross-contamination of profile content
