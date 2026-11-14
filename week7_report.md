# CS263 Project — Week 7 Progress Report: Lean 4 Warm-Start Latency

In Week 5 we found that warm runs are dominated by `import` and bytecode `interpretation`. This week we ran a series of experiments to find out where that time actually goes. They lead to two directions for the rest of the project: **lazy loading of modules** and **compiled tactics for automated proof search**.

**Setup.** `cs292-lean` course repository (Lean `v4.33.1`, Mathlib, Batteries, Aesop via `Course.CourseLib`), Windows 11, warm runs, 1 warmup + 3 measured runs (mean ± SD). Each table below comes from a single session; absolute times were ~2× lower than in Week 5 across all categories (same toolchain and files), so we only compare numbers within one session.

---

## Experiment 1: How Long Do the Imports Take?

**Question.** Week 5 showed that `import` and `interpretation` dominate warm runs, but not how much of that is caused by the imports themselves, as opposed to the work in the file.

**Design.** We timed a file that contains *only* the import line (`import Course.CourseLib`) and nothing else, and compared it with real lecture files (L06, L08).

**Results.**

| | Imports only | L06 | L08 |
| :--- | ---: | ---: | ---: |
| Wall clock | 13.0 s | 14.1 s | 14.9 s |
| `import` | 4.5 s | 4.5 s | 4.5 s |
| `interpretation` | 6.2 s | 6.9 s | 6.9 s |

*(Imports-only and L06 were measured in the same session; L08 in a separate run the same day.)*

The imports alone cost ~10.7 s, which is **77–82% of the runtime** of a typical lecture file, and this is paid on every launch regardless of what the file contains.

**Next question.** There are two ways to make this faster: **speed up importing** (Option A, Experiments 2–3) or **import less** (Option B, Experiments 4–6).

---

## Option A: Speed Up Importing

### Experiment 2: Can the Import-Time Interpreter Work Be JIT-Compiled?

**Question.** Most of the import cost (6.2 s) is `interpretation`: Mathlib's initialization code (registering parsers, tactics, options, attributes, extensions) runs in Lean's bytecode interpreter. Could a JIT compiler, or a faster interpreter, speed this up?

**Design.** We ran `lean --profile` with `profiler.threshold=0` on the imports-only file. This logs every individual entry into the interpreter, so we can see which functions run, how often, and how long each call takes (analyzed with `analyze_interp.py`).

**Results.**
* 7,740 interpreter entries over 4,884 distinct functions.
* **There is almost no reuse.** Only 52 functions are called more than once, accounting for 0.13 s in total.
* **There is no hotspot.** The largest single entry is < 3% of the total; the time is spread across thousands of functions.
* **The first call of a function is 50–350× slower than later calls** (e.g. `Aesop.BaseRuleSet.add`: 6.67 ms on its first call, 0.019 ms afterwards). The interpreter caches within a process, but nothing carries over between launches.

**Takeaway.** A JIT only pays off when the same code runs many times, so that the compile cost is recovered. Here almost every function runs once per launch, so **there is nothing to JIT**. The first-call numbers also show that the cost lies in first-call setup, not in executing instructions, so **improving the interpreter's dispatch** (e.g. threaded dispatch) would barely help either: execution is a small fraction of each first call.

### Experiment 3: Is There Interpreted Work Worth Compiling in the Proofs?

**Question.** If import-time code doesn't repeat, maybe the proof work in the file body does. Is the body's hot code interpreted?

**Design.** The same threshold-0 profiling on L05 and L06, subtracting the imports-only entries to isolate the file body.

**Results.**

| L05 body | Time | Implemented in |
| :--- | ---: | :--- |
| Typeclass inference | 5.2 s | core Lean (native) |
| `simp` | 4.8 s | core Lean (native) |
| Tactic execution | 4.7 s | mostly native |
| Body interpretation | ~1.5 s | interpreted, mostly Mathlib/Batteries **linters** run once per command |

The lecture files use almost only core tactics (`simp` 111×, `grind` 2×) and none of Mathlib's interpreted tactics (`linarith`, `nlinarith`, `norm_num`, `ring`, `aesop`: 0×).

**Takeaway.** Lean's core (the elaborator, `simp`, typeclass inference, the kernel) is **already compiled ahead of time**, and that is where the repeated work happens. A JIT could therefore only target Mathlib's interpreted code, and for these files that is just ~1.5 s of linters. How much a JIT would gain on Mathlib-tactic-heavy workloads (e.g. automated proof search using `nlinarith` or `norm_num`) is still open, since those tactics also spend part of their time calling into native core code.

So speeding up importing through compilation looks unpromising. We went back to the other option: **import less**.

---

## Option B: Import Less

### Experiment 4: How Much Can Minimal Imports Strip Out?

**Question.** The course library imports all of `Mathlib.Tactic`, but each lecture uses only a small part of it. How much faster is a file that imports only what it needs?

**Design.** For L05 and L06 we found the smallest set of imports that still works (Mathlib's `minImports` linter, plus two modules it missed), checked that the compiler output is **identical** to the full-import version, and benchmarked both.

**Results.**

| | L05 full | L05 minimal | L06 full | L06 minimal |
| :--- | ---: | ---: | ---: | ---: |
| **Wall clock** | 28.5 s | **19.7 s (−31%)** | 14.2 s | **9.1 s (−35%)** |
| `import` | 4.5 | 2.3 | 4.5 | 2.1 |
| `interpretation` | 7.6 | 5.3 | 6.8 | 4.4 |
| Typeclass inference | 5.2 | **2.7** | 0.6 | 0.5 |

**Takeaways.**
* Fewer imports speed up **every** phase, including the proofs themselves: with fewer instances and lemmas in scope, L05's typeclass inference halves.
* L06 needs only 3 import lines, but these still pull in **2,178 modules** transitively, so ~6.5 s of import cost remains.

> **Caveat: minimal imports only work for finished proofs.**
> 1. **You must know all your imports ahead of time.** The minimal set is read off the *completed* proof terms, so it can only be computed once the proof is done. It does not work for a proof in progress.
> 2. **It cuts off proof automation's search space.** Automation tactics like `simp`, `grind`, `aesop` and `exact?` work by searching the lemmas and rules that are in scope for ones that apply to the current goal. Importing only what a finished proof used removes everything else from that search, which is exactly what you rely on while you are still looking for the proof.
>
> Minimal imports are therefore useful for re-checking finished files (builds, CI, autograders), but not while writing proofs.

### Experiment 5: Can Lean's Module System Load Less Without Changing the Imports?

**Question.** Lean's newer module system is designed to load less: a `module` file loads only the public interface of its imports (not their proofs), and skips dependencies that a module imports privately. Can it reduce import cost while keeping the full import list?

**Design.** We added a `module` header to L06 (both the full-import and the minimal-import variant), checked that the output is identical, and benchmarked against the non-module versions in the same session.

**Results.**

| L06 | Wall | `import` | `interpretation` |
| :--- | ---: | ---: | ---: |
| Full imports | 14.9 s | 4.8 s | 7.1 s |
| Full imports, module | **13.4 s (−10%)** | **3.7 s** | 6.9 s |
| Minimal imports | 9.3 s | 2.2 s | 4.5 s |
| Minimal imports, module | **9.0 s (−3%)** | **1.9 s** | 4.5 s |

**Takeaway.** A free but small win, and only on `import`. It can't prune more because ~97% of Mathlib's ~26,000 imports are `public`: they are needed in theorem statements, so they must be re-exported. Nearly the same modules are still loaded, and their initialization code still runs.

### Experiment 6: What Does Importing Less Actually Save?

**Question.** Experiments 4 and 5 show that loading fewer modules (or less of each module) helps. Which part of `import` scales with the number of modules: reading the files, or building Lean's in-memory tables?

**Design.** A natively compiled harness (`bench/ImportPhases.lean`) that calls the phases of Lean's own import function directly, with a timer around each.

**Results.**

| Phase (full CourseLib: 4,525 modules, 432k declarations) | Time | Share |
| :--- | ---: | ---: |
| Open and map module files | ~3.2–3.7 s | **~75%** |
| Build the declaration table | ~0.7 s | ~15% |
| Extension states (simp/instance indexes, …) and other | ~0.5 s | ~10% |

**Takeaway.** The dominant import cost is **per-module file access** (~0.75 ms per module), not building tables. Together with each module's one-shot initialization code (Experiment 2), import cost is roughly proportional to the **number of modules loaded**. So the lever is opening fewer modules, but without shrinking the import list, since that would cut off proof automation (Experiment 4 caveat). This leads to our plan for **lazy modules**.

---

## Summary of Findings

1. ~80% of a light file's warm runtime is **startup**: opening thousands of modules and running their one-shot setup code.
2. Startup cost scales with the **number of modules loaded**, not with what the file uses.
3. Reducing modules (minimal imports) removes 31–35% of runtime, but requires knowing the needed imports in advance, which a user writing a proof doesn't.
4. A JIT or compilation does not help startup (code runs once) or the course files' bodies (already native). It can only matter for workloads that **repeatedly run interpreted Mathlib tactics**.

---

## Plan

### 1. Lazy modules (startup latency, benefits every user)
Experiment 4 showed that loading less gives a large speedup (−31–35%), but minimal imports are unusable while a proof is in progress. We want that speedup **without shrinking the import list**: the user keeps `import Mathlib.Tactic`, and Lean loads things lazily behind the scenes.

**Approach.**
* **Load modules lazily.** A module is opened only when something in it is first used (a name, a lemma, an instance, a tactic). A module the file never touches is never opened, never added to the declaration table, and never initialized, removing the per-module costs measured in Experiments 2 and 6.
* **Load parts of modules lazily.** Within a module that *is* loaded, its initialization code (registering tactics, options, attributes, per-tactic rule tables) runs only when that particular piece is first used.
* **Keep the indexes eager.** For automation and lookup to stay useful, Lean must still know about *everything* that was imported. So we build, ahead of time, the indexes that `simp`, `grind`, `aesop`, typeclass inference and name/tactic lookup rely on (which lemmas, instances and syntax exist, and which module each lives in), and load them at startup. Automation then searches the full library, and only the modules containing candidates it actually tries get opened.

In some ways this improves on minimal imports: it works for proofs in progress, it loads only what is actually used rather than whole import closures (L06's 3 minimal imports still load 2,178 modules), and it can skip unused parts within modules. In others it is worse: proof search still considers candidates from the whole library (L05's typeclass inference took 5.2 s with full imports vs 2.7 s with minimal imports), and the prebuilt indexes must be loaded at startup.

### 2. JIT for library modules (stretch goal, if time permits)
Our original goal was startup latency, and for that, lazy modules is the focus. For a human writing proofs, the time spent running tactics is generally not a problem: the heavy work is already native core code (Experiment 3), and once a file is loaded, individual tactic calls are rarely slow enough to notice.

This changes for **large-scale automated (AI) proof search**, which runs Mathlib's interpreted tactics (`nlinarith`, `norm_num`, `ring`, `positivity`, …) thousands of times. That is exactly the repeated, steady-state execution that a JIT needs and that our course files lack, so JIT-compiling library modules could be worthwhile there.

This is heavily caveated: we have not measured how much time such a workload actually spends in interpreted Mathlib code, tactics also spend part of their time in native core code, and a fair comparison against ahead-of-time compilation of Mathlib would require a full Mathlib rebuild. We will look into it only if time permits after lazy modules.
