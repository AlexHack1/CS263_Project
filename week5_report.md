# CS263 Project — Week 5 Progress Report: Lean 4 Runtime & Proof State Loading Latency

## 1. What We Did

### Background Research on Lean 4 Architecture & Profiling
* Reviewed foundational literature on the Lean 4 runtime and frontend architecture, primarily Sebastian Ullrich's Ph.D. dissertation, [*An Extensible Theorem Proving Frontend*](https://pp.ipd.kit.edu/uploads/publikationen/ullrich23thesis.pdf) (KIT, 2023), and de Moura & Ullrich (CADE 2021).
* Mapped the pipeline required to load an interactive proof state in the Lean Language Server:
  1. **Module Import (`.olean` Loading):** Memory-mapping (`mmap`) precompiled binary `.olean` files and initializing persistent environment extensions (`@[init]`).
  2. **Parsing & Macro Expansion:** Converting surface syntax into `Syntax` trees via Lean's extensible Pratt parser.
  3. **Sequential Elaboration:** Top-to-bottom command elaboration, including tabled **typeclass inference**, **tactic execution** (`by ...`), and **simplification (`simp`)** to construct intermediate `MetavarContext` proof states.
  4. **Bytecode Interpretation (`interpretation`):** Running metaprograms, `[init]` hooks, linters, and `#eval` commands in Lean's virtual machine.
  5. **Kernel Type-Checking (`type checking`):** Verifying completed proof terms in the trusted C++ kernel.
* Investigated Section 3.3 (*"The (Future of the) Module System"*) of Ullrich's dissertation regarding how unconditional transitive imports affect load time and search-space scaling.

### Empirical Profiling (`lean --profile`)
* Built an automated Python benchmarking harness (`run_bench.py`) using `lake env lean --profile` to measure end-to-end wall-clock latency and cumulative subsystem times.
* Benchmarked two representative workloads from a Lean 4 course repository (`cs292-lean`, Lean `v4.33.1`, importing `Mathlib`, `Batteries`, and `Aesop` via `Course.CourseLib`):
  * **`L05_ComprehensiveRecap.lean` (74.7 KB):** A large, proof-heavy file exercising tactics (`simp`, `grind`) and algebraic typeclasses.
  * **`L06_FP_Primer.lean` (12.2 KB):** A smaller functional programming file with minimal proofs.
* Recorded **1 cold/warmup run** followed by **3 warm measured runs** (reporting Mean ± StdDev) for each file.

---

## 2. Profiling Results (`L05` vs. `L06`)

*Note: Subsystem profiling categories in `lean --profile` can overlap when child phases (e.g., `simp`, `typeclass inference`) execute inside parent phases (`tactic execution`, `elaboration`).*

| Metric / Runtime Subsystem | `L05` Cold Start (Run 1) | `L05` Warm (Mean ± SD, $n=3$) | `L06` Warm (Mean ± SD, $n=3$) |
| :--- | :---: | :---: | :---: |
| **End-to-End Wall Clock** | **247,880 ms (4.13 min)** | **58,954 ± 1,812 ms (59.0 s)** | **24,575 ± 796 ms (24.6 s)** |
| **`import`** (`.olean` load) | **192,000 ms (3.20 min)** | 6,367 ± 181 ms | 6,887 ± 424 ms |
| **`interpretation`** (Bytecode VM) | — | **17,067 ± 971 ms** | **14,467 ± 896 ms** |
| **`typeclass inference`** | — | **12,933 ± 643 ms** | 1,377 ± 147 ms |
| **`simp`** (Simplifier) | — | **10,600 ± 436 ms** | 177 ± 5 ms |
| **`tactic execution`** | — | **10,130 ± 1,272 ms** | 268 ± 33 ms |
| **`elaboration`** (Core top-level) | 1,120 ms | 1,070 ± 56 ms | 172 ± 12 ms |
| **`tacticAnalysis` + `linting`** | — | 1,049 ± 94 ms | 414 ± 184 ms |
| **`type checking`** (C++ Kernel) | — | 940 ± 99 ms | 201 ± 29 ms |
| **`compilation`** (LCNF + IR) | — | 491 ± 23 ms | 134 ± 16 ms |
| **`process pre-definitions`** | — | 318 ± 38 ms | 78 ± 12 ms |
| **`parsing`** | — | 93 ± 8 ms | 16 ± 1 ms |

---

## 3. Key Findings

1. **Multi-Minute Cold-Start Latency Is Dominated by Disk I/O During `import`:**
   On a cold start (`L05` Warmup 1), loading the file took **247.9 seconds (4.1 minutes)**, of which **192.0 seconds (77.5%)** was spent strictly in **`import`**. Even though Lean 4 `.olean` files are designed for zero-copy memory mapping (`mmap`), a cold OS page cache forces the operating system to open and page in thousands of separate `.olean` binary files from disk into RAM. Once cached in OS memory, `import` drops by **30x** to **~6.4–6.9 seconds** on warm runs.

2. **Transitive Closure of Imports (`Ullrich, Section 3.3`):**
   Although `CourseLib.lean` only contains 8 `import` statements (including `import Mathlib.Tactic`), Lean's traditional module system exports imports unconditionally across the dependency graph. Loading `CourseLib` pulls in the entire **transitive closure** of `.olean` modules required by `Mathlib.Tactic`—even if downstream modules only needed a dependency privately inside a proof. This both inflates `.olean` load times and populates the global environment with thousands of `@[simp]` lemmas and `[instance]` rules, directly increasing **`typeclass inference` (12.9 s)** and **`simp` (10.6 s)** search times in proof-heavy files like `L05`.

3. **High Bytecode `interpretation` Overhead (~14.5 s – 17.1 s) Due to Uncompiled Library Metaprograms:**
   In both `L05` (**17.1 s**) and `L06` (**14.5 s**), the single largest warm-run bottleneck is **`interpretation`**. While core Lean tactics (`Init`/`Std`) are compiled into native machine code inside `lean.exe`, external libraries like `Mathlib`, `Batteries`, and `Aesop` disable native shared-library precompilation (`precompileModules = false`) because compiling and linking thousands of modules into native `.dll`/`.so` binaries would create an enormous compiled disk footprint and hit OS dynamic-linking limits. Consequently, custom tactics, `[init]` environment initializers, linters, and `#eval` blocks from imported packages must execute inside Lean's **bytecode interpreter**, incurring a multi-second baseline overhead on every file load even when proof elaboration itself is tiny (`0.17 s` in `L06`).

4. **Kernel Verification and Parsing Are Negligible (< 2% of Runtime):**
   Parsing (`0.09 s`) and trusted C++ kernel `type checking` (`0.94 s` in `L05`, `0.20 s` in `L06`) account for less than **2%** of total execution time. The latency of loading proof states in Lean 4 is overwhelmingly a **frontend and runtime systems problem** (`import` I/O, VM bytecode interpretation, and typeclass/tactic search) rather than a kernel proof-verification bottleneck.

