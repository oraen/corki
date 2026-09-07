<skills_instructions>
#{catalog}

### How to use skills

- At the start of a turn, skills explicitly named with `$skill-name` are supplied as separate messages after the user input.
- A mention added while the turn is running does not imply its instructions were already supplied. Use `skill_read` if the body is absent, including after context compaction.
- If the task clearly matches a listed description, call `skill_read` for that skill before acting.
- Read `SKILL.md` completely. Load only the referenced files needed for the task.
- Expand a short `file` path using its matching alias in `### Skill roots` before opening it on disk. `skill_read` also accepts the listed skill name directly.
- Resolve scripts, references, templates, and assets relative to the returned `skill_dir`.
- Do not treat a skill as active in later turns unless it is mentioned again or still required by the unfinished task.
- If a skill is unavailable or cannot be read, state that briefly and continue with the best fallback.
</skills_instructions>
