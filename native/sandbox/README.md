# Local permission backend (default integration; overall alignment in progress)

This bridge compiles command policies using `codex-sandboxing`, `codex-protocol`
and `codex-config`, with `codex-execpolicy` / `codex-shell-command` for rule admission,
from Codex commit `ddf04ad26789d040f9ef6a96736f76602e35a6cc`. It does not execute
model commands. Corki owns command startup, I/O, cancellation and cleanup.

Build with Rust 1.95.0, Git and tar; the reference checkout is read-only:

```sh
python native/sandbox/build.py /path/to/codex /absolute/host-owned/bin/corki-sandbox
```

The output and its `.json` receipt must not already exist. `--cargo` selects a Cargo/rustup proxy and
`--offline` requires an already populated dependency cache. `CARGO_TARGET_DIR`
may select an existing build cache. The build exports the pinned commit to a
temporary directory, uses the committed lockfile and never builds in the reference
checkout. Cargo's artifact event identifies the actual output, including target-dir
and cross-target overrides; the receipt binds the compiled Rust-source snapshot,
pinned reference, binary digest, native format/architecture and platform wheel tag.
Keep the executable and Corki installation outside model-writable
directories, just like other host executable code.

For a **local platform-wheel verification build**, supply the fresh artifact:

```sh
CORKI_BUILD_SANDBOX_COMPILER=/absolute/host-owned/bin/corki-sandbox \
  python -m build
```

The Hatch hook verifies the receipt and current bridge source, then includes the
compiler under `corki/_native/sandbox` with platform-specific, non-pure wheel
metadata. Loading uses only that installation directory, never cwd/PATH or a
runtime download. Missing, malformed, symlinked, foreign or damaged assets cannot
silently become unrestricted execution when bundled permissions are requested.
An explicit absolute `execution.compiler` remains a separate host override.
The receipt detects inconsistent assets; it is not a release signature or a
defense against replacing trusted installation code itself.

In that installation, both empty configuration and ordinary SDK
`CorkiSettings(...)` construction select the native implicit profile. Unknown project
trust selects read-only; explicit trusted/untrusted local projects select workspace,
subject to managed constraints and native approval selection. Raw `execution.profile`,
named permission selection or approval policy may omit `execution.compiler`.
These use the same Runtime admission, native policy, approval and OS execution
path as the explicit host compiler. CLI and SDK share the parser; resolved immutable
settings retain their captured compiler/selection across copies and model updates.
Missing default assets fail before model/tool admission; there is no unrestricted
fallback. The temporary config-default template does not require a bundled backend
before reading a valid explicit host compiler override.

Only an explicit SDK `execution_permissions=None` retains the previous embedding-host
execution path. It is not selected by omission or TOML, does not implement native
command admission, and cannot bypass managed execution requirements. Native
`default_permissions=":danger-full-access"`/`profile={type="disabled"}` remain separate:
they still use the compiler and command rules, even though OS sandboxing is disabled.
This compatibility host opt-out is not counted as Codex-equivalent enforcement.

For source development, after building the artifact above, run:

```sh
python native/sandbox/install.py /absolute/host-owned/bin/corki-sandbox
```

It validates provenance against this bridge source and installs a fresh
`src/corki/_native/sandbox` directory without overwriting an existing one. These are
Git-ignored host build artifacts, not checked-in portable source. Editable imports
use that directory. A direct `python -m build --wheel` detects it and emits a
platform wheel; the two-stage sdist→wheel command above still needs the explicit
build variable because portable sdists do not carry host binaries. Builds with
neither source assets nor an explicit build variable remain generic wheels, whose
default runtime requires an explicit host compiler or a native platform installation.

Default activation does not establish whole-Harness or cross-platform equivalence.

Skill MCP installation consent uses the bridge's `full_disk_write_access` result
from the pinned `FileSystemSandboxPolicy`, together with its effective approval
policy. This includes Managed unrestricted/root-write profiles, but rejects root
write narrowed by read-only/deny entries. Python does not infer write authority
from readable roots or profile labels. Explicit older compilers without this
classification cannot auto-approve installation and must be rebuilt; this does not
silently disable OS enforcement. The SDK's explicit `execution_permissions=None`
host path remains distinct. OAuth installation and the remaining Harness audit
are not established by this permission classification.

Restricted project-instruction and image reads use the fixed native
`--corki-fs-helper` entrypoint. A separate compiler contract is required; older
explicit compilers fail closed and must be rebuilt. Only the helper transform
adds the pinned executor's Minimal/self-executable read permissions and restricted
network policy. Retained user permissions, ordinary commands and terminal snapshots
do not receive these grants. Supplying another argv cannot select a different
program under the helper grants. The helper accepts only fixed file operations,
uses bounded input/output and nonblocking regular-file reads; image validation
processes the returned bytes in the host. Its release environment allowlist is
PATH/TMPDIR/TMP/TEMP plus the macOS CoreFoundation startup variable, captured per
invocation (not yet the native runner's construction-time environment snapshot).
The current regular-file bridge is Unix-only and has real enforcement tests only
on macOS. Project discovery retains override/fallback, ancestry, byte-budget and
all-candidates-before-reads behavior, tested against the Python path.

Configured `apply_patch` now requires the separate `native_patch` compiler capability
and runs pinned `codex-apply-patch` parsing, whole-patch verification, core safety
assessment and application inside that fixed helper. The compiler captures the
original permissions and host policy context independently of helper runtime reads.
Permission rejection happens before the first write. Native execution is sequential:
a later I/O failure can leave earlier changes committed; errors report the committed
count, delta exactness and a bounded path sample, never promise rollback or retry.
Add/move overwrite and default LF normalization follow the reference engine.
The native crates' external dependencies are locked to reference versions/checksums.

Patch preparation is a separate no-write native operation. `AskUser` decisions
use the Runtime execution approval handler and CLI through a dedicated host-only
`patch_approval` kind, including patch text, changed-file details and move targets.
Accept permits one attempt under the captured sandbox; decline does not write;
cancel interrupts the Turn and dismisses the review. Session approval caches each
local environment/file key and covers subsets, but not new destinations. Shell
rule amendments cannot authorize patches. Missing handlers fail closed, and older
compilers cannot silently omit the required `native_patch_approval` contract.

The additional `native_patch_delta` contract returns ordered committed changes,
including overwritten source/destination text and delta exactness, on successful
and partially failed attempts. The host keeps this bounded JSON in the tool ledger
and completion event, not model history. Missing/invalid/oversized post-start output
is an unknown, inexact outcome, never success or an automatic retry. Cancellation
still propagates as control flow.

Each local logical Turn now owns a net text-diff tracker, shared with nested Code
Mode calls. Ordered committed records feed baseline/current/origin state and a
revision-based render cache, without rereading the workspace. Rendering includes
the pinned core tracker source and uses its Git blob IDs and 100ms content-exact
fallback. Transient TurnDiff events follow tool completion and completed model
steps; an empty event clears net-zero or invalidated output. Interrupted/unknown
attempts invalidate a prior diff; final clearing cannot block on an abandoned UI
queue. Cold Corki resumes rebuild from this Turn's ledger and deduplicate call IDs,
never replay writes; incomplete legacy evidence disables that recovered aggregate.

The `native_patch_retry` v1 capability adds v2 attempt outcomes with original
stdout/stderr, exit status and the pinned sandbox-denial predicate. Only an
eligible classified denial may request one fresh, one-time host approval for a
fixed-helper bypass. Never, disabled granular sandbox approval and explicit
denied reads prohibit bypass. Unlike the native already-approved shortcut, this
adapter always requires fresh retry review with the first attempt's committed
delta and uncertainty; session consent cannot automatically replay an uncertain
write. Lost/malformed transport never admits retry. Rejected retry approval keeps
the first result; a second attempt appends its delta without erasing prior
uncertainty. Cancellation remains control flow and there is no third attempt.

Bypass applies the retained verified patch with native no-follow operations,
without an unrestricted re-verification read. Otherwise-required sandboxes alone
cause no-follow; the ordinary full-access path still follows links. The predicate
is a native heuristic (including "failed to write file"), not proof of the OS
error's cause. The host reviews that original evidence before granting authority.
Multiple-environment tracking and experimental preserve-line-endings are not yet
implemented. Initial approval is not sandbox bypass, so an approved out-of-policy
write can still fail in the first attempt. Environment selection is rejected. The
explicit SDK `execution_permissions=None` compatibility path retains its older
Python transaction/parser behavior; it is not the default native path. These
remaining differences prevent a whole patch/Harness alignment claim.

Workspace metadata protection includes `.corki` alongside `.git`, `.agents` and
`.codex`. The small additive `patches/workspace-metadata.patch` is applied only to
the temporary pinned source export, never the reference checkout. It extends the
native metadata-name list, default project-root and concrete carveouts, and Linux
bwrap's missing-metadata handling. It does not replace path matching, insert Python
read grants, or rewrite generated sandbox text. The patch is included in build
provenance; a missing/drifted patch fails the build. New command/context requests
require Corki metadata semantics, so older explicit compilers reject admission and
must be rebuilt, rather than silently retaining the previous `.corki` write gap.

Real macOS tests cover existing/missing directories, both shell surfaces and the
patch helper, internal/external links, explicit read/write/deny and child overrides,
extra roots and system aliases. Native precedence still matters: a linked target
with an independent writable-root grant may remain writable through that grant,
while the protected metadata link itself cannot be deleted or renamed. Mutable
symlinked writable roots are rejected by native Seatbelt; full-access/external
profiles retain their declared authority. Linux patch application is checked, not
equivalent to Linux OS execution proof. Existing runtimes should be closed before
replacing their compiler; retained terminal evidence is not retroactively upgraded.

Bundled executable validation currently covers thin 64-bit macOS and Linux formats; actual bundled
OS-effect proof is macOS only. macOS 11+ tags use `major_0` and may not understate
the binary's recorded minimum OS. Linux libc compatibility, Windows packaging,
lower-OS execution, signing and complete redistribution notices remain unverified.
Do not publish these local verification wheels as supported public releases.

When changing bridge dependencies, `--update-lock` updates this bridge package in
the exported workspace and copies the generated lockfile back before the locked
build. Ordinary builds do not change the lockfile. Set both `CARGO_HOME` and
`RUSTUP_HOME` when using isolated rustup proxies, including for rustfmt.

Raw-profile Corki configuration (separate from the named form below):

```toml
[execution]
compiler = "/absolute/host-owned/bin/corki-sandbox"
profile = { type = "workspace-write", network_access = false, exclude_tmpdir_env_var = true, exclude_slash_tmp = true }
```

The profile accepts native tagged `PermissionProfile` or legacy `SandboxPolicy`
JSON/TOML object forms. They are distinct wire contracts; the host dispatches
legacy policy variants to the compiler's legacy input. Policy resolution uses the
host workspace root, not a model-selected command workdir. Ordinary and Code Mode
dispatch share this snapshot. Shell/PTY, patch and local image access use it.
The file helper performs actual filesystem access under the native helper profile.
Backend failures return tool errors; only the separately reviewed patch-denial
path above can retry outside the sandbox.

Host consumers require a single typed `ok` or `error` result from this bridge and
the owned filesystem helper. Mixed results, duplicate JSON object keys, invalid
Unicode/non-finite numbers and incorrect success payload types are rejected before
admission, retry decisions, rule publication or permission-context caching. JSON
inside a string payload remains content for that consumer, not another envelope.
Malformed rule-write acknowledgements warn without repeating a possibly completed
write or claiming live publication; command approval itself remains a separate decision.

With explicit execution settings, Runtime loads `rules/*.rules` from the admitted
user and project config directories before thread creation, MCP startup or model
sampling. Directly constructed settings without config sources retain the home
directory fallback. Each directory is sorted; only
regular `.rules` entries are read, not symlinks. Missing directories are empty;
other directory/read errors and invalid UTF-8 reject startup. A syntax error
discards the complete user-rule set and emits one startup warning. Explicit
host-owned `ExecPolicySource` texts are composed after these files. Project
`.corki` directories use the same trust gate as project config, even without a
config.toml file. This is the local user/project stack, not the complete
system/cloud/MDM/session config-layer stack.

Validated sources are captured as an immutable execution snapshot. File changes
do not silently change a running Runtime; cold creation/reopening reloads them.
A failed startup retries from the original host inputs. Copying settings into a
non-root Runtime does not itself inherit rules: an explicitly supplied
`inherited_exec_policy` can be reused only when its config folders, declared
sources and native managed-policy identity match. Native fingerprints ignore rule
ordering but retain source identity; a changed policy or source reloads user files.
Fresh background memory sessions reload instead. Basic guardian hosts omit user
rules but retain managed rules. A ready runtime exposes `execution_policy_handle`
for an embedding host to pass as `inherited_exec_policy` to an explicitly created
non-root runtime. Matching native-admitted snapshots share subsequent rule updates
and the update lock, but retain independent approval routers, session consent,
write destinations, processes and shutdown. Handles are same-event-loop resources;
cross-loop live sharing is rejected before thread/model startup. They are not
settings/checkpoint data. Passing an `ExecPolicySnapshot` still means immutable
snapshot reuse, not live sharing. No automatic parent lookup or full agent-spawn
orchestration is provided by this API.

Managed `requirements.toml` also supports `[rules].prefix_rules`. Each pattern
position specifies `token` or `any_of`, and its explicit decision must be `prompt`
or `forbidden` (`allow` is invalid in requirements). The native layer composer
preserves all layers' rules, high-priority first; command evaluation combines them
with user rules and chooses the strictest matching decision. User allow rules
cannot cancel managed denials. Malformed user files discard only user rules;
malformed managed rules are fatal configuration errors. The native
`managed_exec_policy` capability and effective identity are checked before use.
Memory passes retain their captured caller's managed rules independently of stale
service base settings. This is command-rule support, not managed approval/network
orchestration.

Ordinary and Code Mode shell commands share the pinned native policy gate. The
configured TOML default is OnRequest; an explicitly untrusted active project uses
UnlessTrusted internally. Directly constructed `ExecutionPermissions` still defaults
to Never, under which unmatched dangerous commands and explicit prompt rules are
rejected. Compound shell commands are checked segment by segment. Only explicit
allow coverage for every segment permits a sandbox bypass, and independent read
denials still prevent that bypass. Host-executable matching and dangerous-command
classification use the upstream parser rather than Python string heuristics.
Explicit interactive review and local startup-denial retries are supported as
described below. Full managed-network/reviewer orchestration remains incomplete.

To enable rule-driven review, set top-level `approval_policy = "on-request"` before
the `[execution]` table and rebuild the native compiler. `on-failure` is accepted as
an alias; a `granular` policy table follows Codex's protocol fields. Explicit
`untrusted` configuration is rejected (that policy is an internal project-trust
state). Without an execution compiler, an explicit approval policy is an error.

Managed `allowed_approval_policies` is parsed and composed by the pinned native
requirements implementation. Rebuild the compiler for its `managed_approval`
acknowledgement and effective-policy response. A disallowed default falls back to
the first allowed value; a disallowed explicit choice also falls back and emits
a startup warning. Empty lists and malformed policies reject startup. Granular
policies are compared as complete native values, not by variant name alone.
Approval resolution precedes sandbox fallback, and Runtime publishes the effective
policy to both tools and model context. Per-command checks cannot silently change
that published policy. An older compiler cannot ignore managed approval constraints.

The host snapshot retains the original request separately for configuration
re-admission. Hosts changing that request must update `requested_approval_policy_json`
(or construct a fresh `ExecutionPermissions`); changing only the effective field is
not a new configuration selection. Background memory derivation replaces the parent
approval constraint with an internal Never-only constraint, as in native phase two;
other managed execution constraints and rules remain in force.

The CLI asks for consent before executing a rule-prompted or dangerous command.
Embedded hosts bind `runtime.set_execution_approval_handler(handler)` and reply
with `runtime.respond_execution_approval(request_id, "accept" | "decline" | "cancel",
remember=False)`. The handler receives a host-owned `shell_approval` request;
MCP responses cannot resolve these tokens. Missing handlers refuse required review.
Only explicit acceptance with `remember=True` caches consent for matching command identity,
cwd, tty, requested sandbox permissions and managed-environment rule fingerprint within this Runtime's session.
Session-cache entries are not persisted across a cold Runtime restart; saved rules
described below are separate authority.

Approval of a default command does not remove the sandbox. External Turn cancellation dismisses and
joins pending host delivery and preparation before any model command starts;
an approval form's cancel decision also interrupts the active Turn. A decline
instead becomes a tool error and allows model continuation. Only a valid pending
shell-review token can cancel: stale/completed tokens and MCP responses cannot
interrupt a later Turn. The Python event stream reports `TurnCancelled` before
raising cancellation, as for other host interrupts. Memory worker
derivation and basic guardians retain Never and do not inherit interactive review.
The compiler must acknowledge `shell_approval` and return a typed `exec_approval`
field; incomplete contracts cannot silently execute. This does not yet implement
additional permissions, Guardian/hook review or network prompts.

Rebuild for `sandbox_retry_supported` to enable native local startup-denial retries.

Rebuild for `terminal_review_supported` and set `[features] write_stdin_approval = true`
to enable local retained-terminal input review (default false, matching the pinned
experimental feature). Direct and Code Mode calls retain host-owned permissions
from the actual successful launch, including an unsandboxed denial retry. Empty
polls and non-TTY interrupts bypass review. Ordinary unchanged default permissions
also need no review. Input exceeding current permissions requires fresh once-only
approval; earlier command/session approval and saved command rules do not authorize
stdin. Never and granular policies disabling sandbox approval reject this review.
Unenforceable new denied-read restrictions require a new terminal, not approval.

The review includes complete input, launch cwd (not an inferred current terminal
directory), process identity and permission intent. NUL or review text over 8,000
UTF-8 bytes is rejected without truncating and then executing an unseen tail. Local
UUID session handles differ from Codex's integer handles, so boundary byte counts
include that compatibility representation. The interaction lock spans review and
process identity is checked again before writing. Cancellation/close joins review
delivery and native comparison helpers; denial leaves the terminal available.
Old compilers cannot silently enable this feature with unknown launch permissions.
This local domain does not add strict Guardian, extra grants, environment-owned
network proxies, remote executor policy snapshots, or cross-OS enforcement.

The host privately owns the first process for the native 150 ms early-exit window.
Only an exited, non-timeout attempt classified by the pinned native predicate can
retry, and only once. An untrusted policy or a granular policy allowing sandbox
approval may request this retry; Never and OnRequest do not. Independent denied
reads and an already-unsandboxed first attempt prevent a bypass.

A command already approved for this call reuses that approval. Otherwise, the host
asks explicitly whether to retry without the sandbox, after retiring the first
process. Decline, cancel, classifier failure and unknown outcomes do not launch a
second attempt. Once a live process is published for polling, later failure cannot
re-enter the startup retry path. Both Direct and Code Mode use the same owner and
original command/cwd/environment. Ordinary final nonzero exits still return normal
command observations with their exit code and output; they are not tool-dispatch
failures. A retry may repeat partial effects of the first failed command; this is
the native policy/approval behavior, not a claim of command idempotence.

Older compilers without this capability retain the previous no-automatic-retry
path. New capability/plan fields are checked before launching any command. This
does not implement managed-network exception retries, strict Guardian re-review,
remote executor denial metadata or a new default sandbox backend.

With the rebuilt compiler, `exec_command` accepts `sandbox_permissions` values
`use_default` and `require_escalated`, in both Direct and Code Mode. The latter
requests unsandboxed execution for this command only. Never, or Granular with
sandbox approval disabled, rejects this request before explicit allow rules can
bypass the gate. Otherwise the native command policy decides whether approval
is required; restricted unmatched commands request approval, and explicit allow
rules may skip it. Required approval without a host handler fails closed.

Independent denied-read restrictions still prevent unsandboxed execution, even
after approval. Neither the stored permission profile nor subsequent commands
inherit this override. The approval cache retains the original requested intent,
including when denied reads force a default sandboxed launch. Optional
`justification` requires an explicitly supplied `sandbox_permissions`; it is used
as the prompt reason only if the command policy has no reason. Escalation itself
does not require a justification. The compiler must acknowledge `model_escalation`;
an old compiler cannot silently ignore the override. Without configured execution
permissions this request fails rather than implicitly granting unrestricted access.
Additional permission requests and sticky Turn/session grants remain unfinished.

`prefix_rule` now supplies a candidate to the pinned native policy evaluator. It
does not authorize execution or write a rule. The evaluator filters banned or
ineffective prefixes, checks all parsed commands, and applies native heuristic
fallback suggestions only where appropriate. Existing explicit prompt rules do
not generate a conflicting allow proposal. The CLI displays the candidate and
offers once/session/rule scopes when a candidate is available. Embedded hosts use
`respond_execution_approval(..., execpolicy_amendment=[...])` to approve a rule;
that is a distinct decision from `remember=True` and cannot be combined with it.

The host appends to its configured home `rules/default.rules` through the native
advisory-lock/deduplication implementation, then checks whether the latest complete
policy already allows the proposed prefix before publishing a live session overlay.
The native check includes captured user rules, shared live prefixes and managed
constraints; it uses native executable matching with Forbidden fallback, not Python
string-prefix comparison or the current model's allow-rule filtering. A narrower
rule may be saved on disk without a redundant live update or context notification.
Snapshot sources are not reloaded from the just-written file. The shared update
lock covers capturing this policy, the write, and publication.
The home directory must already exist. Native append creates the `rules` directory,
not missing ancestors. The current already-approved command keeps its original
compiled sandbox intent; subsequent commands and cold Runtime creation use the
saved rule. Append failures emit a warning and keep only the current approval;
they do not publish a rule or cache session consent. Cancellation joins an
already-authorized write and publication before completing, without executing the
model command. This is not an atomic-rename/fsync transaction and failed/unknown
writes are not retried automatically.

Rebuild the compiler for the independent `execpolicy_amendment_published` boolean
acknowledgement. A written-only or malformed response does not grant new live
authority: the current approval is retained, a warning reports the uncertain live
outcome, and no retry is attempted. Old bare native append requests remain supported
with an empty initial policy; Runtime always supplies its current captured policy.

Model catalog `model_specialty="cyber"` now suppresses user allow-prefix rules and
rule proposals in both Direct and Code Mode, while preserving prompt/forbidden
and managed rules. This does not implement Guardian review or managed
`auto_review.ignore_rules`, which remains an unsupported managed domain. Live
automatic parent lookup/fork orchestration remains incomplete. Explicit live policy
handles share saved prefix updates, including after the originating runtime closes;
ordinary memory startup and basic guardian isolation are unchanged.

With the new compiler, approval-cache identity uses the original executable and
the pinned canonical command: one simple shell command is tokenized natively;
complex scripts retain exact text (and Bash shell mode). Cwd, tty, requested
permissions and the managed rule fingerprint remain independent key components.
Old compilers may support basic review but cannot silently ignore requested rule
options: `exec_policy_amendments` must be acknowledged. Native canonicalization is
compiled directly from the pinned exported source; when formatting this bridge
outside a build export, use rustfmt with `--config skip_children=true` and list the
local modules explicitly so it does not follow the export-only module path.

Permission context now uses the pinned `codex-prompts` renderer with the same
effective rule parser/filter/managed overlay as command admission. Runtime sends
the actual sandbox/network state, approval policy, writable roots and denied-read
restrictions as developer context. Saved rules append only a new-prefix notice;
unchanged policy does not repeat the block, and removed rules or a post-compaction
missing baseline refresh the full block. Cold recovery retains comparison state.
Top-level `include_permissions_instructions = false` selects Codex's compact
mode: the initial rule set is silent, while later additions are still reported.
Unconfigured legacy execution also receives its Disabled/Never description,
without requiring a compiler. This does not change Corki's default execution policy.

Configured execution needs a rebuilt compiler acknowledging `permission_context`.
Effective user-role environment snapshots additionally require versioned
`environment` metadata from the permission renderer. Rebuild and install the
compiler together with the Python change; old binaries reject the new workspace
root field rather than silently omitting effective authority. The renderer uses
the post-constraint profile, not raw configuration. Environment snapshot v2
refreshes old four-field baselines once and preserves append-only history.
An incompatible renderer fails the Turn before model sampling; it does not invent
permissions, remove the sandbox, or silently omit the context. Rendered sections
have a 30 KB / 10,000 estimated-token hard limit; oversize is an explicit error,
not truncated policy. Model-specific approval/permission message overrides now
travel from `models.catalog.<model>.model_messages.approvals` / `.permissions`
through typed admitted model metadata and both durable snapshot codecs. Missing
or null text falls back; an empty string suppresses the corresponding section.
Only the exact `{{ network_access }}` placeholder in permission text is replaced;
approval text and other placeholders remain literal. The pinned bundled catalog's
overrides are preserved too. This changes model-visible text, not the execution
policy: empty Never instructions do not authorize sandbox escalation.

Custom text uses the admitted Turn's model metadata, including during Step model
switching, matching this pinned reference's legacy world-state consumer. A later
Turn can use a different model's text. Approval-text overrides can hide the initial
approved-prefix list but do not suppress subsequent saved-prefix delta messages.
The legacy compiler-free Disabled/Never path follows the same rendering semantics.
The compiler must acknowledge `model_permission_messages` when overrides are sent.
Automatic reviewers and extra permission tools remain unimplemented; storing the
auto-review-specific catalog text does not activate that reviewer. The native renderer may
produce over 1,000 tokens: its input is host-owned policy, not tool output; the
section is budgeted with the full request and cached only for one effective view.

Rebuild the compiler: startup requires typed `exec_policy_loaded` and captured
sources; command admission requires typed `exec_policy_checked`. Old/incomplete
contracts fail closed. A compiler missing at startup is a startup error; a compiler
failure after successful admission becomes a tool error with no unrestricted retry.
Owned filesystem helpers and memory profile derivation do not enter the model
command gate. The bridge's existing 4 MB response limit also bounds returned rule
snapshots; it does not silently truncate a policy.

Only macOS Seatbelt enforcement has been integrated/tested. Linux/Windows managed
backends are not delivered; requests needing an unavailable backend fail closed.
Disabled/External preserve native ownership semantics, but External requires the
caller to provide real isolation. A restricted External network value does not
create an additional local sandbox by itself.

Missing `[execution]` retains Corki's old unrestricted local execution behavior.
Background memory passes capture the admitted parent's explicit execution settings,
separately from the memory service's base configuration. Before sampling, the native
compiler derives the worker profile: Disabled/External retain their enforcement;
Managed gets only its memory workspace writable, no network, and no default
TMPDIR or `/tmp` write exceptions. Derivation failures fail the owned memory job
with `failed_sandbox_policy`; they do not start an unrestricted worker. Rebuild the
compiler when upgrading from the initial command-only bridge: older executables
reject the new request and fail closed.

Managed `allowed_sandbox_modes` and `[permissions.filesystem].deny_read` can now
come from the system requirements file or host-supplied `MCPRequirementsLayer`
fragments. The existing snapshot API retains its name for compatibility; it carries
independent immutable execution layers as well as MCP authority. The system file
uses its parent directory for relative paths; host fragments may specify an absolute
`base_dir`. Neither workspace config nor model arguments select these sources.
The pinned native config parser composes layers: deny-read is additive, not ordinary
array replacement. It classifies sandbox modes by concrete file permissions.

Managed execution requires a configured compiler/profile. Runtime resolves it before
thread creation, MCP startup or model sampling. Disallowed selections may fall back
to read-only with a warning; a full-access selection cannot fall back when approvals
are disabled (Corki's current shell execution path). Commands revalidate constraints
without fallback. Memory workers retain the captured managed layers and validate
their derived policy without re-reading later system requirements. User profile
denies and independent managed denies are not interchangeable. Older compilers that
cannot acknowledge managed requirements fail closed.

AGENTS discovery uses the same explicit permission boundary. The compiler now
returns `full_disk_read_access` from the native filesystem policy: unrestricted
reads stay host-side, while restricted discovery and reads use the fixed owned
filesystem helper inside the compiled sandbox. Restricted failures reject startup
before sampling. Older compilers without this classification fail closed for this
path; rebuild the bridge when upgrading. Independent global home instructions are
host-provided and are not discovered through the project's filesystem policy.

User named profiles can instead use the following form:

```toml
default_permissions = "build"

[permissions.build]
extends = ":read-only"

[permissions.build.filesystem.":workspace_roots"]
src = "write"

[execution]
compiler = "/absolute/host-owned/bin/corki-sandbox"
```

Do not combine this form with `execution.profile`. The fixed source export also
compiles Codex's original `core/src/config/permissions.rs` module: inheritance,
special/scoped paths and workspace root expansion are not reimplemented in Python.
The resolved identity and profile-added roots are published with the effective
profile before sampling. Managed mode fallback clears them; memory derivation
clears the parent's identity and roots. They are not yet persisted/restored.

With only `execution.compiler`, unknown active-project trust selects read-only.
`[projects."/absolute/project"].trust_level` accepts `trusted` or `untrusted`;
both select workspace permissions through the pinned native default selector
(Windows with its sandbox disabled remains read-only). An explicit profile or
approval policy overrides these defaults. Explicitly untrusted projects omit
project AGENTS instructions; unknown trust does not suppress those instructions.
Selection checks canonical then logical cwd keys, followed by the verified Git
trust root. Linked worktrees require metadata/backlink/common-directory ownership
checks. An empty cwd project entry masks the root entry. Settings capture this
decision at parsing, without rereading it for each tool workdir.
Without `[execution]`, the legacy host path remains. This is not the separate
project-config layer trust gate. Corki's local loader separately admits
`.corki/config.toml` and rules only from trusted layers; full system/cloud/MDM/
session layering and legacy sandbox selection syntax remain incomplete.
Named `network.enabled` is supported; other profile network
proxy options fail explicitly until proxy orchestration exists.

Managed requirements also accept named `permissions` tables,
`allowed_permission_profiles` and `default_permissions`. User/managed profiles may
inherit in either direction, but sharing an ID is an error. Every allowlist ID must
exist (even a false entry). A managed default must be allowed; omitting it requires
both `:read-only` and `:workspace` to be allowed and selects `:workspace`.
Disallowed selection IDs fall back before policy compilation. This differs from
mode-constraint fallback, which clears the selected identity.

Admission retains the original user catalog for retries. Concrete tool and memory
overrides carry it for catalog validation without reselecting the parent's profile.
The ID allowlist constrains named selection, not every explicit concrete override;
mode and independent deny-read constraints still apply. This does not implement
automatic host-config reload or durable permission recovery. The compiler must
advertise `managed_catalog` support; rebuild older bridge executables before use.

Other managed domains (including approval/network requirements), approval
orchestration, durable permission snapshots, current-Turn overrides and remote
execution are NOT complete. Unsupported managed domains remain
explicit errors. Memory phase2 now derives its worker configuration before DB input
selection, synchronization and the no-change decision. That same configuration and
owned workspace are passed to the child Runtime; skipped/failed passes reclaim the
unused workspace, and unconfirmed Runtime shutdown retains its existing owner.
The main consolidation pipeline uses the actual shared memory root for both policy
derivation and tool cwd. A managed deny on that root cannot be bypassed by copying
inputs to a different path. Temporary worker state is owned separately and its
cleanup never removes the shared root. Tool writes are immediately visible; worker
failure does not roll back acknowledged writes. After confirmed close, file-editing
workers are validated in place and the owner fence controls baseline/job completion,
not a second copy-back publication. Symlinks are removed without following targets.

Standalone `run_agent` calls without a prepared shared pass retain the isolated
compatibility path. Final JSON output is also a separate compatibility path. The main
pipeline now uses a real internal Git HEAD/index baseline (host Git required), including
hidden files and executable modes, with read-only diffs and one-commit resets that discard
old objects. V3 JSON snapshots migrate without losing known pending changes. Only owned
internal Git metadata may be replaced; an unrelated project repository fails closed.
Diff hunk rendering remains provider-neutral Python rather than native `similar::TextDiff`.
Full configuration, layout/claim interactions and recovery remain under audit; do not
infer whole-phase2 equivalence from this step.
Do not treat this bridge as whole-Harness permission alignment.

OS-effect tests require the built backend explicitly:

```sh
CORKI_TEST_SANDBOX_COMPILER=/absolute/host-owned/bin/corki-sandbox \
  python -m pytest tests/integration/test_execution_permissions.py \
    tests/integration/test_memory_execution_permissions.py \
    tests/integration/test_managed_execution_permissions.py \
    tests/integration/test_named_execution_permissions.py \
    tests/integration/test_managed_named_permissions.py \
    tests/integration/test_exec_policy_runtime.py \
    tests/integration/test_exec_policy_loading.py
```

Absent the test backend, OS-effect tests skip; backend-error and lifecycle tests
still run. A skipped test is not enforcement evidence. No real model selection
quality or Codex Rust test suite is asserted by these ScriptedModel-style tests.

The wrapper source is Apache-2.0. Exported Codex source retains its upstream
license; dependency source/checksum records are pinned in Cargo.lock. Local platform
wheel assembly and loading are supported as described above; complete dependency
license notices and release distribution verification remain open.
