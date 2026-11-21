# CS263 Project — Week 8 Progress Report: Incremental Re-elaboration in Lean 4

## Change of Direction

In Week 7 we found that loading imports costs ~10.7 s, about three quarters of the runtime of a light lecture file (L06, L08), and we planned to reduce it with lazy loading. For a heavier file like L05 (28.5 s), the imports are less than half of the runtime; the rest is checking the file's own proofs. Looking at how people actually use Lean, we have changed focus. Startup is annoying, but it is paid **once**, when a file is opened. The cost that is paid **repeatedly** is re-checking a file after every edit. For files with larger proofs this is slow, and it is what the user waits for during normal work.

*Note:* lazy loading remains a valid way to cut startup and is future work. Its gain is limited, however: automation still needs prebuilt indexes over the whole library, which must be loaded at startup, and our best measured case (minimal imports) saved 31–35%.

## The Observation

A Lean file is processed as a sequence of **commands**: each `theorem`, `def`, `instance`, `#eval`, `open`, etc. is one command. When the user edits a command, Lean re-elaborates that command **and every command below it**.

```lean
def double (n : Nat) := n + n
theorem a : double 1 = 2 := by simp [double]   -- edit this proof...
theorem b : 5 = 5 := rfl                        -- ...and b, c, d are re-checked too
theorem c : double 3 = 6 := rfl
theorem d : 2 + 2 = 4 := by decide
```

This is not logically necessary. `b`, `c` and `d` do not depend on how `a` was proven, so their results cannot change. Lean re-checks them anyway because it treats the file as a chain: each command's input is "everything above it". Lean's own source acknowledges this ("ideally we would decide what can be reused ... using strong hashes over the full state and inputs, currently we rely on simpler syntactic checks", `Lean/Language/Lean.lean`).

## The Idea: A Dependency Graph Inside the File

Build systems such as Bazel (Blaze) solve the same problem across files: they know what each build step reads and writes, and after a change they re-run only the steps that read something that changed. We want to do the same thing *within* a single Lean file, with commands as the steps.

For each command we record:
- **Reads**: what the command used while it was being checked: declarations it referred to by name, definitions it looked inside (unfolded), and collections it searched (the `simp` set, instances, `aesop`/`grind` rules).
- **Writes**: what the command added: its declarations (their statements and, for definitions, their bodies) and any entries it added to those collections.

When a command is edited (or a new one is added), Lean re-checks it and compares its new writes with its old ones. It then walks down the file and re-checks only the commands whose reads include something that changed. If a re-checked command's own writes change, those changes are added to the list, so the effect propagates as far as it actually needs to, and no further.

## The Rules

| What was edited | What has to be re-checked |
|---|---|
| A theorem's **proof** | Nothing else. No command can see a proof, only the statement. |
| A theorem's **statement** | Commands that used that theorem. |
| A definition's **body** | Commands that looked inside (unfolded) that definition. |
| A lemma tagged `@[simp]` (or `@[aesop]`, `@[grind]`, an `instance`) | Commands that searched that collection, e.g. every later `simp` call. |
| Anything else (notation, `open`, options, …) | Everything below, as today. |

Some consequences of these rules:
- **Theorems are firewalls.** Re-checking a theorem can only change its proof, never its statement, so the propagation stops at every theorem. This holds even if the new proof fails: Lean still adds the theorem with its stated type (and reports an error), so commands below see exactly the same thing.
- **Definitions are not firewalls.** Their bodies can be unfolded later, e.g. `rfl` proving `double 3 = 6` computes through `double`. This is why reads are recorded *while* Lean checks a command, not read off the finished proof: the finished proof `rfl` never mentions `double`.
- **Automation reads whole collections.** A `simp` call in `b` may be affected by a new `@[simp]` lemma in `a` even though `b` never mentions `a`, so `simp` records that it searched the `simp` set.
- **A few commands look at proofs** (`#print`, `#print axioms`), so they are always re-checked.

## Implementation So Far: Proof-Only Edits

Code: our fork of Lean 4, [github.com/AlexHack1/lean4](https://github.com/AlexHack1/lean4), branch [`incremental`](https://github.com/AlexHack1/lean4/tree/incremental), based on Lean `v4.36.0`.

We are building the rules in stages, starting with the first row of the table: **after an edit to a theorem's proof, reuse the results of every command below it.** This stage needs no read/write recording, because nothing below can be affected. It does need the infrastructure all later stages share: keeping results below an edit instead of discarding them, and moving them to their new positions in the file.

Changes:
- **Locating the edit** (`src/Lean/Language/Lean.lean`). Lean already computed where an edit starts; we also compute where it ends and how many bytes it added or removed.
- **Deciding** (`checkReuseBelow`, same file). Reuse is allowed only if: the edited command is a `theorem` whose name, attributes and statement are unchanged and the edit lies inside its proof; the theorem still ends exactly where the edit says it should, so the text below is unchanged; every command below finished its earlier check without being interrupted; and none of them looks at proofs (`#print`, `#print axioms`, `#reduce`). Otherwise Lean behaves exactly as before.
- **Reusing** (`parseCmd`, same file). Where Lean used to re-parse and re-check everything after the changed command, it now attaches the old results of the commands below.
- **Moving positions** (`src/Lean/Language/Basic.lean`, `src/Lean/Elab/InfoTree/Basic.lean`, `src/Lean/Language/Lean/Types.lean`). Reused results still refer to their old positions. Since the text below an edit is unchanged, everything after the edit moves by the same offset. Syntax positions are moved immediately; error messages and the goal/hover information are moved when the editor reads them, through a hook Lean already had for a similar purpose. Two places in the language server that read results directly were updated to apply the same shift.
- **Switch** (`Elab.reuseBelowProofEdit`). A new option, on by default, so the old and new behaviour can be compared with the same build.
- **Test** (`tests/server_interactive/reuseBelowProofEdit.lean`). Uses Lean's language-server test runner: commands below the edit print a marker whenever they are checked. Checks that a proof edit reprints nothing; that the option off, a statement edit, or a `#print axioms` below do reprint; and that error messages and goals below are reported at their new positions.

Known limitation: "go to definition" for declarations below an edit can point a few lines off, because those positions are stored inside the reused results.

**Status:** the fork is being built; the test and timing measurements (editing a proof near the top vs. the bottom of L05, with the option on and off) come next.
