# Design

## Context

See [proposal.md](proposal.md) for motivation. Relevant current state:

- Specialists subclass `BaseAgent` (`agents/base.py`), are singletons in `SPECIALIST_REGISTRY` (`orchestrator/router.py`), and are fanned out in parallel by `route_parallel`. Each specialist's `analyze()` call receives retrieved knowledge, failure cases, department memory, and a collapsed `company_stage`/`principal_role` line — but never the raw company profile or another specialist's context.
- The Executive alone holds the company profile, in `prompts/cache_manager.py::build_system_blocks()` Block 1 (5-minute TTL) — this block is never sent to a specialist.
- ChromaDB isolation is enforced per-collection (`knowledge/store.py`), not just by a `domain` metadata filter: `ATTACHMENT_COLLECTION` and `RESEARCH_COLLECTION` are kept out of retrieval entirely for the same reason personal data needs its own collection.
- There is no existing personal/company data split anywhere in the codebase — this is new ground, not an extension of an existing mechanism.

## Goals / Non-Goals

**Goals:**
- Add `personal_assistant` as a ninth specialist with its own prompt, domain alias, and area name, following the exact structural pattern of the existing 8 specialists.
- Give it its own profile model, storage location, and ChromaDB collection, with no code path that can cause personal data to reach a business specialist or vice versa.
- Keep the Executive's prompt-caching design untouched: no new content enters the existing cache blocks.

**Non-Goals:**
- Building any new UI for personal profile onboarding (a CLI/manual `personal/profile.yaml` edit is sufficient for v1, mirroring how `company/profile.yaml` started).
- Any calendar/email provider integration for personal accounts (e.g. a personal Google/Outlook calendar) — this change only adds the specialist and its data store; wiring live personal calendars is a separate future change.
- Cross-referencing personal and company data (e.g. "block my calendar for this business trip AND remind my family") — out of scope for v1; the isolation requirement in specs/personal-data/store/spec.md explicitly forbids this until a deliberate, reviewed exception is designed.

## Decisions

**1. New specialist, not an Executive-level capability.**
Chosen per user direction. Keeps the "Executive is sole synthesizer, specialists are backend-only" invariant intact (`orchestrator/executive.py`), and reuses `route_parallel`'s existing failure isolation, fan-out cap, and tool-schema wiring for free. Alternative considered (Executive handles personal requests directly) was rejected because it would require a second, parallel tool-calling path outside `consult_specialist`, doubling the surface CI and the arch-docs process need to track.

**2. Dedicated `personal_docs` ChromaDB collection, not a `domain` value inside `company_docs`.**
The codebase's own precedent (`knowledge/store.py`, `RESEARCH_COLLECTION`/`ATTACHMENT_COLLECTION` comments) is explicit: "the collection is the boundary; a domain value never was." A metadata-only split would rely on every future query path remembering to filter correctly — a single missed filter would leak personal data into a business specialist's retrieval. A separate collection makes the omission a "collection doesn't exist in this query" error instead of a silent leak.

**3. Personal profile injected only into the specialist's own user-turn context, never a cache block.**
Mirrors how `company_stage` reaches business specialists today (a collapsed single line in the user turn, per `base.py:129-134`), rather than reusing the Executive's Block 1 pattern. This keeps the existing 2-block cache design (persona+knowledge-index / company-context) completely unchanged — no new cache_control block, no risk of breaking the 10x-cost-sensitive caching behavior the CLAUDE.md flags as critical. Because the personal profile is expected to be small and rarely change between turns for a given conversation, no caching is added for it in v1; it can be revisited if profile size or call volume make that worthwhile later.

**4. `personal/` directory mirrors `company/` exactly (profile.yaml + docs/), gitignored the same way.**
Lowest-surprise choice for anyone already familiar with `company/profile.yaml` / `company/docs/` and `cli/fixture_loader.py`'s "profile + docs + collection are one unit" pattern. `PersonalProfile` is a separate Pydantic model from `CompanyProfile` (not a subclass/reuse) since the fields differ entirely (people/relationships/personal preferences vs org structure/financials) and coupling them would create pressure to eventually share a cache block.

**5. Domain alias and `_AREAS` entry added like any other specialist; no new tool schema shape.**
`consult_specialist`'s `specialist` enum is `sorted(SPECIALIST_REGISTRY.keys())` already — adding `"personal_assistant"` to the registry dict is sufficient; no manual schema edit needed beyond the description map.

## Risks / Trade-offs

- **[Risk]** A future contributor adds a convenience helper that reads "the profile" generically and accidentally wires `personal/profile.yaml` into a company-context code path (or vice versa). → **Mitigation**: name the loader functions and settings keys unambiguously (`load_personal_profile` vs `load_company_profile`, `PERSONAL_PROFILE_PATH` vs `COMPANY_PROFILE_PATH`), and add the integration test from proposal.md's Impact section that asserts personal data never appears in a business specialist's retrieved knowledge or the Executive's cache blocks — this is a regression test, not just a unit test, specifically to catch this failure mode.
- **[Risk]** `route_parallel`'s fan-out cap (`resolve_fanout_cap`, default = registry size) is sized to today's 8 specialists; adding a 9th doesn't break the cap since it scales with registry size, but per-turn latency/cost goes up slightly whenever both a business specialist and the personal assistant are consulted together. → **Mitigation**: none needed beyond existing fan-out cap behavior; note as an accepted trade-off.
- **[Risk]** No calendar/reminder delivery mechanism exists yet, so "personal_assistant" can only reason over profile/doc content, not actually schedule or notify. → **Mitigation**: explicitly a non-goal for this change; document in the PR description that live scheduling/reminders are a follow-up.

## Migration Plan

Purely additive — no existing data or behavior changes. Deploy steps:
1. Add `personal/` to `.gitignore` (with `.gitkeep`), matching the existing `company/` entry.
2. Ship code (agent, prompt, registry entries, collection, settings) — inert until a `personal/profile.yaml` exists; absence should degrade gracefully (empty/no personal profile, specialist still answers from general knowledge only, analogous to how a missing `company/profile.yaml` behaves today).
3. No rollback complexity: reverting the PR removes the specialist and store with no migration needed, since nothing else depends on them.

## Open Questions

- Exact on-disk shape of `PersonalProfile` fields (e.g. which relationship/family fields to model) is left to implementation-time judgment in `tasks.md`, since it doesn't change the isolation contract or the specialist's routing behavior — any reasonable v1 schema satisfies the specs.
