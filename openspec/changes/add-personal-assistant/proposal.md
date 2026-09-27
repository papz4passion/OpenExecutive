# Proposal

## Why

Open Executive currently only tracks and advises on the principal's professional/company life. The principal also wants help with personal life admin — scheduling, reminders, family and relationship commitments — through the same assistant, without any personal data leaking into the business specialists' context or the company knowledge base.

## What Changes

- Add a new `personal_assistant` specialist agent (ninth specialist, alongside the 8 business specialists), registered in `SPECIALIST_REGISTRY`, covering two domains: general personal life-admin/scheduling (appointments, reminders, travel, household tasks) and family/relationships (birthdays, family events, social commitments, gift reminders).
- Add a `PERSONAL_ASSISTANT_PROMPT` domain prompt.
- Add an `_AREAS` entry (`"personal_assistant": "personal life"`) so the chat UI can name this area when the specialist can't answer.
- Add a `DOMAIN_ALIASES` entry for retrieval filtering.
- Introduce a fully separate personal-data store, isolated from all company data:
  - A new gitignored `personal/` directory (mirroring `company/`): `personal/profile.yaml` (structured personal profile — analogous to `company/profile.yaml`) and `personal/docs/` (uploaded personal documents/notes).
  - A new dedicated ChromaDB collection (e.g. `personal_docs`), never mixed into `company_docs`, `builtin_knowledge`, or any other existing collection — consistent with this codebase's "the collection is the boundary" isolation principle.
  - A `PersonalProfile` model + loader, analogous to `CompanyProfile`, but never included in the Executive's cached system prompt blocks (Block 0/Block 1) — instead injected only into the `personal_assistant` specialist's own user-turn context, the same way `company_stage` is passed to business specialists today (never into a shared cache block).
- Business specialists (`cso`, `cfo`, `chro`, `gc`, `coo`, `cmo`, `cpo`, `sales`, `board_comms`, `triage`) get no access to the personal store; the `personal_assistant` specialist gets no access to `company_docs`/company profile. The Executive orchestrates both but never blends personal content into the company-context cache block.
- Add eval scenarios for the new specialist under `packages/core/openexecutive/evals/_scenarios/personal_assistant_001.yaml` and `_002.yaml`, using a `personal_context` field in place of `company_context`.
- Update architecture docs (`agents` section, `integrations`/`schemas` as applicable) per this repo's Architecture Docs process.

## Capabilities

### New Capabilities
- `orchestrator/personal-assistant`: registration, routing, and prompt behavior of the new `personal_assistant` specialist, including its two supported domains and its isolation from business-specialist routing/context.
- `personal-data/store`: the gitignored `personal/` directory, `PersonalProfile` model, and dedicated ChromaDB collection that hold personal data, and the guarantee that this data is never merged into company data stores or the Executive's shared cache blocks.

### Modified Capabilities
(none — no existing capability's requirements change; this only adds new ones)

## Impact

- New files: `packages/core/openexecutive/agents/personal_assistant.py`, `packages/core/openexecutive/memory/personal_profile.py` (or similar), `packages/core/company` analog under `packages/core/personal/` (gitignored, with `.gitkeep`).
- Modified files: `prompts/domain_prompts.py` (new prompt constant), `orchestrator/router.py` (`SPECIALIST_REGISTRY`, `SPECIALIST_DESCRIPTIONS`), `orchestrator/answer_sources.py` (`_AREAS`), `knowledge/retriever.py` (`DOMAIN_ALIASES`), `knowledge/store.py` (new collection constant), `config.py` (new settings: personal profile path, personal docs path), `.gitignore` (new `personal/` entry).
- New tests: unit tests for the new agent, retriever domain alias, and profile loader; integration test confirming personal data never appears in a business specialist's retrieved knowledge or the Executive's company-context cache block.
- Architecture docs: `architecture/prebuilt/agents.json` (new specialist entry) and `architecture/architecture-facts.yaml` (new isolation invariant), per the repo's required Architecture Docs update for a new routing/agent pattern.
- No breaking changes — purely additive.
