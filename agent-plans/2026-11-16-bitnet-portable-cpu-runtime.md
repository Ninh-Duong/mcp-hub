# BitNet CPU Compatibility and Performance Matrix

Status: in-progress

## Context

The installed BitNet runtime crashes before `/health` becomes available. Windows Application Error events show `llama-server.exe` failing with `0xC000001D` (illegal instruction) on three starts. The local AMD Ryzen 7 7730U reports AVX2 support and no AVX512F. The BitNet stderr log was empty, consistent with a CPU exception before the process could report an error.

The release path currently builds and publishes one Windows x64 runtime. `scripts/build_bitnet_runtime.py` does not pin the CPU instruction set. The pinned BitNet commit `0b341e582afbf9e1011f24744b554c96a3477eb5` resolves llama.cpp to `390c307752ab78fd8189f359d6954c9ba1be74af`. That llama.cpp revision defaults to native CPU optimization and its ClangCL/MSVC path probes the build host, so a newer release runner can produce an artifact that executes unsupported instructions on the user's CPU. AVX-512 is the leading explanation for this incident; the exact faulting opcode is unconfirmed without a dump.

The pinned BitNet source maps x86-64 I2_S to AVX2-capable kernels and ARM64 I2_S to NEON kernels. The ARM kernel has a DOTPROD fast path and a NEON fallback. I found no evidence of a scalar I2_S fallback for x86 CPUs below AVX. Upstream source supports ARM64, but the pinned docs do not establish a tested Windows ARM64 release target. Therefore “all CPUs” means all CPU families that this product explicitly supports and ships a verified runtime for; it cannot literally mean every processor. Installation cannot add an unsupported ISA to hardware.

## User stories

### US1 (P1): Install BitNet on supported Windows x64 CPUs

As a Hub user on an AVX2-capable Windows x64 machine, I can install and start the same reproducible release runtime regardless of the CPU used to build it.

Acceptance: the release x64 runtime is built with a documented AVX2 baseline, runs the pinned model, and passes `/health` smoke coverage on the Ryzen 7 7730U class as well as the release runner.

### US2 (P1): Get a clear result on unsupported hardware

As a user on a CPU or architecture without a compatible BitNet runtime, I learn why BitNet cannot run before downloading the runtime or model, while the Hub's deterministic fallbacks remain available.

Acceptance: installer preflight rejects unsupported or unknown targets before network access and returns a fixed, localized error code with no raw exception or hardware identifier.

### US3 (P2): Use ARM64 CPUs where the Windows target is validated

As a Hub user on a supported Windows ARM64 device, I can use a separate NEON runtime selected for that architecture.

Acceptance: the ARM64 runtime is published only after the pinned source, Windows toolchain, installer selection, and real ARM64 smoke target pass the release gates. Until then, Windows ARM64 fails clearly as unsupported.

### US4 (P2): Improve speed without sacrificing portability

As a user with a newer supported CPU, I can benefit from a faster runtime when it is measurably faster and selected only on compatible hardware.

Acceptance: an AVX-512 build or runtime-dispatch package is added only when a repeatable benchmark shows material benefit over the AVX2 baseline and the artifact is selected safely; otherwise the portable AVX2 binary remains the sole x64 runtime.

## Functional requirements

- FR-001 Build the Windows x64 release with `GGML_NATIVE=OFF`, explicit required AVX2-era flags, and explicit AVX-512/VNNI exclusions verified against the pinned CMake revision.
- FR-002 Keep the build reproducible: pin upstream revisions, toolchain inputs, target architecture, and manifest asset metadata; do not inherit CPU flags from the build host.
- FR-003 Before downloads, detect OS and architecture and check the selected runtime's minimum ISA. Unknown architectures and missing required CPU features fail closed.
- FR-004 Select an asset from an explicit manifest/runtime matrix. Never guess an asset name or silently fall back to an incompatible executable.
- FR-005 Keep BitNet thread selection independent from ISA selection; `settings.bitnet.threads` and the current auto-thread behavior remain unchanged unless separate benchmark evidence justifies a later change.
- FR-006 Ship Windows ARM64 only after upstream/toolchain compatibility and an ARM64 runtime smoke target are verified. NEON is the baseline; DOTPROD is optional and must be dispatched or packaged separately if used.
- FR-007 Keep unsupported-machine behavior graceful: fixed error code, localized UI message, no model/runtime download, and existing deterministic planning/condensing fallbacks.
- FR-008 Add offline unit tests for build flags, CPU/architecture detection, manifest selection, fail-closed behavior, and localized error mapping.
- FR-009 Release validation must execute the packaged artifact with the pinned model on representative hardware for every published architecture/ISA tier; a smoke test only on the build runner is insufficient.
- FR-010 Evaluate optional AVX-512 or runtime-dispatch variants with a repeatable benchmark on representative AVX2 and AVX-512 systems before deciding to ship them.
- FR-011 Update the owning BitNet documentation and both locale bundles with supported targets, prerequisites, install errors, and fallback behavior.
- FR-012 Run the focused tests, `uv run python scripts/package_qc.py`, and `uv run python scripts/smoke.py`; do not lower coverage or skip offline test requirements.
- FR-013 Run Ponytail review after implementation and remove unnecessary abstraction, duplicate detection logic, and variants without measured value while preserving the compatibility matrix.

## Success criteria

- SC-001 The emitted Windows x64 binary has no build-host ISA dependency and explicitly targets the verified AVX2 baseline.
- SC-002 A unit test fails if `GGML_NATIVE` is re-enabled or required/forbidden ISA options drift.
- SC-003 The user's AVX2/no-AVX512 CPU passes the packaged-runtime `/health` and model-response smoke test; the release runner also passes.
- SC-004 CPU/architecture detection and asset choice are deterministic and tested, including AVX2 absent, ARM64, unsupported x64, unknown architecture, and supported x64.
- SC-005 Unsupported hardware does not start network calls and receives a localized fixed error; deterministic Hub fallbacks continue working.
- SC-006 ARM64 is either fully validated and published as a distinct asset or explicitly reported unsupported; it is never served the x64 binary.
- SC-007 Optional AVX-512/runtime dispatch is shipped only if benchmark results meet an agreed material speedup threshold without regressing the AVX2 tier. The threshold and benchmark protocol are recorded before implementation of this optional variant.
- SC-008 Focused tests, `package_qc.py`, and `smoke.py` pass, and Ponytail review has no unresolved simplification findings.
- SC-009 Docs and locale bundles state the tested compatibility matrix accurately; no claim promises support for literally every CPU.

## Edge cases

- AVX2 support detection must account for Windows OS support for saving extended processor state, not only the raw CPUID bit; use a documented Windows capability API or a small isolated probe.
- ARM64 Windows is a distinct OS/architecture target from ARM64 Linux/macOS and must not be inferred to work solely from upstream's ARM source kernels.
- ARM NEON is the required baseline for the planned ARM target; DOTPROD is an optional acceleration and must not leak into the baseline binary.
- CPUs below the upstream-supported x86 ISA cannot gain AVX2 by installing software. Do not emulate AVX2 or claim a slow scalar fallback unless one is implemented and validated upstream.
- A portable AVX2 baseline may be slower than a CPU-specific build on some processors. Any faster variant needs separate compatibility metadata and evidence from repeatable benchmarks.
- Runtime multi-variant dispatch may require dynamic backend loading and may interact with BitNet's custom kernels. Verify a proof build before selecting it; otherwise prefer separate, explicit assets.
- An existing installation may contain the incompatible runtime. A new release and reinstall/update path are required; fail-safe behavior for a failed update must preserve or clearly mark the previous install.
- No BitNet failure may disable core Hub routing or deterministic fallbacks.

## Check against AGENTS.md

- Read `README.md`, `docs/bitnet.md`, `agent-plans/README.md`, and the BitNet contract before implementation.
- Keep source, tests, errors, and docs in English; add locale values in English and Vietnamese together.
- Add offline unit tests for each behavior change and import every changed core module from tests.
- Do not inspect or change `vendor/`, `logs/`, private configuration, or secret-bearing files.
- Keep the one-start-path rule: runtime selection/install remains in the existing BitNet preflight/installer flow.
- Run `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py` before completion. Ask before raising the version after QC passes.
- Keep this plan `draft` until the user explicitly approves the design.

## Decisions

- Treat support as an explicit compatibility matrix, not a promise for literally every CPU.
- First guarantee a single reproducible Windows x64 AVX2 baseline. Keep the current model, thread setting, and deterministic fallback behavior.
- Make CPU/architecture detection and graceful early rejection part of the same fix so deployments do not download gigabytes before learning the machine is unsupported.
- Include Windows ARM64 as a gated target in this plan because upstream's pinned source has ARM64 I2_S kernels, but publish it only after a real Windows ARM64 build and smoke target validate it. Until that gate passes, ARM64 remains clearly unsupported.
- Keep AVX-512 optimization as a benchmark-gated decision. Do not add an AVX-512 artifact or dynamic dispatch speculatively.
- Treat x86 CPUs below AVX2 as unsupported for BitNet I2_S unless upstream or a separately scoped implementation demonstrates a correct fallback; retain Hub deterministic behavior.
- Use an explicit runtime manifest/asset matrix and fixed error codes; do not duplicate hardware detection in the desktop UI.

## Tasks

### T001: Lock the supported CPU and Windows target matrix

**Files:**
- Read: `README.md`
- Read: `docs/bitnet.md`
- Read: `scripts/build_bitnet_runtime.py`
- Read: `src/mcp_hub/bitnet/installer.py`
- Read pinned upstream sources: BitNet `setup_env.py`, I2_S kernels, llama.cpp CPU CMake and SIMD probes

- [x] Confirm the pinned BitNet/llama.cpp revisions and the native-ISA build-host leak.
- [x] Confirm x86 I2_S AVX2 and ARM I2_S NEON paths; record that Windows ARM64 needs a real target validation.
- [x] Resolve ARM64 as a gated follow-up: the current product publishes Windows x64 only; do not publish ARM64 without a real supported smoke target.
- [x] Record the pinned CMake AVX/AVX2 options and explicit BMI2, AVX-VNNI, and AVX-512 exclusions; ClangCL's `/arch:AVX2` path supplies the AVX2 compiler target.

### T002: Add regression tests before implementation

**Files:**
- Test: `tests/test_build_bitnet_runtime.py`
- Test: `tests/test_bitnet_installer.py`
- Test: `tests/test_desktop.py`
- Test: `tests/test_desktop_window.py`

- [x] Add a command-capture test asserting the x64 CMake baseline and forbidden extensions.
- [x] Add injected capability-probe tests for supported x64, unsupported x64, Windows ARM64, unknown architecture, and OS/CPU probe failures.
- [x] Assert unsupported targets fail before release lookup, downloads, or extraction.
- [x] Keep the existing single x64 asset contract; unsupported targets cannot select an asset.
- [x] Add fixed localized error rendering tests that never expose raw exception text.
- [x] Run the targeted tests and confirm the new behavior assertions fail before implementation.

### T003: Build the portable x64 runtime

**Files:**
- Modify: `scripts/build_bitnet_runtime.py`
- Modify: `tests/test_build_bitnet_runtime.py`

- [x] Set `GGML_NATIVE=OFF` and the verified explicit AVX2 baseline; disable newer instructions supported by the pinned CMake revision.
- [x] Keep upstream/model/toolchain pins and package only dependencies required by the resulting binary.
- [ ] Run the existing build script smoke test with the pinned model on a Windows release runner; this workspace has no CMake/Clang/dumpbin toolchain.

### T004: Gate and select runtime assets by compatibility

**Files:**
- Modify: `src/mcp_hub/bitnet/installer.py`
- Modify: `src/mcp_hub/bitnet/setup.py` only if existing public installer state needs a fixed error code
- Modify: `src/mcp_hub/dashboard/locales/en.json`
- Modify: `src/mcp_hub/dashboard/locales/vi.json`
- Modify relevant tests from T002

- [x] Add a small injectable platform/architecture/ISA detector using supported Windows APIs; keep it independent of download logic.
- [x] Check compatibility before disk/release/network work and return one fixed error code per unsupported condition.
- [x] Keep the existing single-asset contract; non-x64 and unknown combinations fail closed.
- [x] Preserve checksum/size validation, atomic `.part` download, and safe extraction behavior.
- [x] Localize unsupported CPU and architecture messages in every locale.

### T005: Validate Windows ARM64 as a separate target

**Files:**
- Modify: `scripts/build_bitnet_runtime.py` only if the pinned source/toolchain can produce the ARM64 target
- Modify: `.github/workflows/desktop-release.yml`
- Modify: `src/mcp_hub/bitnet/installer.py`
- Modify relevant tests and release manifest schema

- [ ] Prove the pinned BitNet custom kernels and ClangCL toolchain build Windows ARM64 with NEON baseline.
- [ ] Add a distinct ARM64 asset and manifest entry only after the target produces a valid executable.
- [ ] Run `/health` and a model-generation smoke test on actual Windows ARM64 hardware or an officially supported ARM64 runner.
- [x] Leave ARM64 unsupported and defer its asset until those gates can be run; current installer rejects ARM64 before network access.

### T006: Benchmark optional x64 speed tiers

**Files:**
- Add or modify: `scripts/` benchmark helper only if existing tooling cannot run a repeatable local benchmark
- Add or modify: benchmark documentation only if a new procedure is introduced

- [ ] Record a benchmark protocol for the pinned model, fixed prompt/output, warmup, thread counts, and tokens/sec on representative AVX2 and AVX-512 machines.
- [ ] Compare the portable AVX2 baseline against an AVX-512 candidate and any feasible upstream runtime-dispatch mode.
- [ ] Add a faster tier only if the measured gain meets the threshold agreed in T001 and compatibility selection can be verified offline.
- [x] Ponytail review found no measured case for a second runtime or dynamic dispatch; keep those additions deferred until benchmark hardware and evidence exist.

### T007: Document support and deployment behavior

**Files:**
- Modify: `docs/bitnet.md`
- Modify locale bundles if not completed in T004

- [x] Document the x64 AVX2 baseline, ARM64 unsupported status, below-AVX2 behavior, install preflight, and separate thread/ISA settings.
- [x] Explain that installation cannot add CPU instruction support; unsupported systems keep deterministic Hub fallbacks.
- [x] Ensure docs describe only assets that release CI actually builds and validates.

### T008: Full verification and Ponytail review

**Files:**
- Verify all files changed by T002–T007

- [x] Run focused BitNet build/installer/desktop tests (123 pass).
- [x] Run `uv run python scripts/package_qc.py` (1,161 tests, 92% coverage, all checks pass).
- [x] Run `uv run python scripts/smoke.py` (Hub passed; one unconfigured child reported WARN).
- [ ] Verify the packaged binary on the Windows release runner and on representative published CPU tiers.
- [x] Run Ponytail review; one portable x64 artifact is the smallest supportable solution without benchmark hardware. No extra runtime variants or dependencies added.
- [x] Asked after QC; user approved the MINOR bump to 0.8.0. The release runtime smoke remains a CI gate before publication.

## Analysis

| Requirement | Tasks |
|---|---|
| FR-001, FR-002 | T001, T003, T008 |
| FR-003, FR-004 | T002, T004, T005 |
| FR-005 | T003, T007 |
| FR-006 | T001, T005 |
| FR-007 | T002, T004, T007 |
| FR-008 | T002, T003, T004, T005 |
| FR-009 | T003, T005, T008 |
| FR-010 | T001, T006 |
| FR-011 | T004, T007 |
| FR-012, FR-013 | T008 |
| US1 / SC-001–SC-003 | T001–T003, T008 |
| US2 / SC-004–SC-005 | T002, T004, T007 |
| US3 / SC-006 | T001, T005, T007 |
| US4 / SC-007 | T001, T006, T008 |
| SC-008–SC-009 | T007, T008 |

## Open questions

- Should Windows ARM64 be a first-release target or a separately approved follow-up? The plan currently includes it behind strict build and real smoke gates.
- What measured AVX-512 speedup is material enough to justify a second runtime? Set a threshold before implementing that optional tier.
- Can release CI provide Windows ARM64 hardware for runtime smoke tests, or should ARM64 remain unsupported until such a runner exists?
- After implementation and QC, the user must choose whether to raise the version so a new runtime can be released.

## Progress log

- 2026-11-16 — Codex: Implemented explicit AVX2 CMake targeting, fail-fast Windows x64/AVX2 checks, fixed localized CPU errors, and docs. New tests were observed failing before the implementation. Targeted suite passed (123 tests); package QC passed (1,161 tests, 92% coverage); smoke passed with one unconfigured child warning. Fixed the unrelated child test's 1-second startup race by increasing its timeout to 3 seconds. User approved version 0.8.0; lockfile changed only for the package version. Ponytail review kept one x64 artifact and deferred ARM64/AVX-512 variants without validation hardware. Remaining: real runtime build/smoke on Windows release CI before publication.
