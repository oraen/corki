<skills_instructions>
#{catalog}

### How to use skills

- If the user names a skill with `$skill-name`, its complete instructions are supplied separately for this turn.
- If the task clearly matches a listed description, call `skill_read` for that skill before acting.
- Read `SKILL.md` completely. Load only the referenced files needed for the task.
- Resolve scripts, references, templates, and assets relative to the returned `skill_dir`.
- Do not treat a skill as active in later turns unless it is mentioned again or still required by the unfinished task.
- If a skill is unavailable or cannot be read, state that briefly and continue with the best fallback.
</skills_instructions>
