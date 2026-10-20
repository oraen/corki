---
name: skill-installer
description: Discover, compare, and install Corki skills. Use when users ask for a skill for a task, want recommendations or available skills, or ask to install from a skill name or GitHub repository (including private repos).
metadata:
  short-description: Find suitable skills and install them into Corki
---

# Skill Installer

Help users find an appropriate skill and install it into Corki. Combine open-ecosystem discovery with the bundled Python installer; do not use a second installer with different directory rules.

Run bundled scripts relative to this skill's actual directory, using the Python environment that runs Corki. Do not assume the current working directory is the skill directory.

## Choose the workflow

- **“Find a skill for this task” / “Which skill should I use?”**: inspect installed skills, then follow [discovery](references/discovery.md). Recommend a small set of relevant candidates.
- **“What can I install?”**: list the curated catalogue by default, identify its source, and offer broader search when useful.
- **Known skill name**: resolve its repository and real directory; a name alone does not uniquely identify a source.
- **GitHub repository/path supplied**: inspect that source and use the installation steps below. No ecosystem-wide search is needed.
- **Explicit installation request**: proceed within the user's scope; do not ask again for permission already given. A discovery-only request does not authorize installation.

## Local and curated catalogues

```bash
python scripts/list-skills.py --local --format json
python scripts/list-skills.py --format json
python scripts/list-skills.py --repo openai/skills --path skills/.experimental
```

The remote default is `openai/skills`, directory `skills/.curated`; it is one source, not the complete ecosystem. `--repo`, `--path`, and `--ref` select other repository directories. Local inventory covers Corki extension, shared, and built-in skills, including their metadata names and actual paths. Installed does not necessarily mean enabled; inspect Corki's skill configuration if that matters. Do not reinstall an existing skill just because it is disabled.

## Installation

1. Resolve the actual directory containing `SKILL.md` at the selected repository/ref. Search results such as `owner/repo@skill` identify a candidate, not its directory. Inspect the repository; never guess `skills/<name>`.
2. Read its instructions and relevant scripts/dependencies before recommending installation. Explain any required credentials, executables or services. Installing files does not install those dependencies.
3. Select the destination:
   - Default: extension skills in `$CORKI_HOME/skills` (fallback `~/.corki/skills`).
   - User requests shared/public skills: `--scope public`, using `~/.agents/skills`.
   - Built-in skills live in `$CORKI_HOME/skills/.system`; do not install third-party skills there.
4. Preview when useful, then install with the same verified source and destination:

```bash
python scripts/install-skill-from-github.py --repo owner/repo --path path/to/skill --dry-run
python scripts/install-skill-from-github.py --repo owner/repo --path path/to/skill
python scripts/install-skill-from-github.py --url https://github.com/owner/repo/tree/main/path/to/skill --scope public
```

Replace example source values with verified values. Multiple `--path` values install multiple skills. `--ref` selects a branch/tag/commit; for branch names containing slashes, use explicit `--repo`, `--ref`, and `--path` instead of an ambiguous URL. `--dest` is for an explicitly requested custom skills root; explain if Corki does not scan it.

New installs require an explicit `name`, a nonempty description, and matching skill-directory/name. The installation directory comes from the metadata name. Do not rename skills to bypass a collision. Existing legacy skills are not migrated. The installer checks metadata names across built-in, extension and shared skills, and refuses existing destinations. It never overwrites an installed skill.

Downloads are preferred for public GitHub repositories. Authentication/permission download failures can fall back to Git sparse checkout (HTTPS, then SSH). Private repositories use existing Git credentials or `GITHUB_TOKEN`/`GH_TOKEN`; do not request credentials in chat or put them in command arguments. `--method auto|download|git` controls retrieval.

After installation, report the installed names, exact paths, and any remaining dependency setup. Verify the local catalogue sees the files; they should be available on the next turn when Corki reloads skills. If a running session does not refresh, use a new session. Do not claim a skill's external service works merely because installation succeeded.

## Failure and update handling

- Missing Node.js/npm blocks CLI-based discovery, not the Python GitHub installer. Use the web/repository fallback in the discovery reference.
- Network/authentication failures are not empty search results. Report the actual limitation; do not fabricate candidates or repeatedly retry unchanged requests.
- A conflict is not permission to delete, rename or overwrite the existing skill. Explain the conflicting name/path.
- Do not use `npx skills add` or `npx skills update` for this workflow: Corki's Python-installed files do not share that CLI's installation tracking. Updates/replacements require an explicit request and preservation of local changes.
- If no suitable skill is found, explain what was searched and help with the task directly, or suggest Corki's `skill-creator` for a reusable custom skill.
