# LLM Governance Challenges

Issues that arise when applying organizational reasoning frameworks to LLM agent systems, and strategies for mitigation within the AORTA model.

## The Action–Reasoning Boundary

AORTA governs **actions** — discrete, inspectable events at the boundary between agent and environment. In LLM agent systems, actions manifest as tool calls: `write_file(Path)`, `read_file(Path)`, `execute_command(Cmd)`. These are structured, have typed parameters, and are amenable to logical reasoning.

Hallucination and confabulation happen inside the agent's **reasoning process** — the probabilistic token-by-token generation that produces text. This is opaque to any external governance layer. There is no belief base to inspect, no derivation chain to verify, no logical structure to constrain.

This is the fundamental architectural tension: AORTA's power comes from the clean separation between deterministic governance (Prolog) and flexible reasoning (LLM). Hallucination sits entirely on the LLM side of that boundary.

### What governance can see

```
Agent calls: Write(file_path="docs/requirements.md", content="...500 words of prose...")
                  ↑                                          ↑
                  Structured, checkable                      Opaque natural language
                  (Prolog can reason about paths)            (Prolog cannot reason about claims)
```

Governance checks the **action envelope** — who is writing, where, whether norms permit it. The **payload** (file content, command strings, prose) passes through unchecked because evaluating it requires the very NLU capability that is unreliable.

### What governance cannot see

- False factual claims embedded in text ("Tool X requires Feature Y")
- Unjustified confidence in uncertain conclusions
- Omissions — things the agent should have mentioned but didn't
- Logical non-sequiturs in reasoning chains

These are properties of the agent's internal reasoning, not of the action boundary. No Prolog predicate can express `content_contains_false_claim(Text)` without invoking NLU — which reintroduces the exact non-determinism the governance layer exists to avoid.

## Why BDI Agents Didn't Have This Problem

In Jensen's original work, AORTA governed BDI agents (Jason, 2APL) that had **explicit belief bases**. If a Jason agent believed something, that belief was a first-class logical term:

```prolog
believes(agent1, requires(claude_code, git))
```

You could write norms about beliefs, inspect them, require justification. The agent's internal state was structured and accessible.

LLM agents have no such structure. Their "beliefs" are distributed across billions of parameters and manifest only probabilistically in outputs. The internal state is a black box. AORTA's metamodel has no predicate for LLM beliefs because there is nothing to bind a variable to.

## Approaches That Don't Work

### LLM-checking-LLM

Using a second LLM to verify the first LLM's claims does not add deterministic guarantees. The verifier is subject to the same hallucination risk. This is defense-in-depth (which has value) but not the kind of formal guarantee AORTA provides for action governance.

### Content-level Prolog predicates

Expressing content constraints as Prolog conditions:

```yaml
- role: architect
  type: forbidden
  objective: write_file(Path)
  condition: file_contains_unverified_claim(Path)
```

This requires `file_contains_unverified_claim` to parse natural language and identify factual claims — NLU at its hardest. The condition cannot be evaluated deterministically by Prolog.

### Forced structured decomposition

Requiring the LLM to decompose all output into structured `DeclareFact(claim, source)` calls before writing prose. Architecturally clean, but:

1. The LLM can skip the decomposition and embed claims directly in Write content
2. The decomposition itself is performed by the LLM, so it can hallucinate the decomposition
3. It fights the generative nature of LLMs (token-by-token, not claim-then-prose)

## What AORTA Can Do: Organizational Discipline

Human organizations face the same problem. No process can prevent an individual from being wrong. But good organizations create structures — peer review, citation requirements, research mandates, separation of concerns — that make errors less likely and more catchable.

This is AORTA's natural territory. The framework can enforce **epistemic discipline**: organizational norms that require agents to follow processes known to reduce confabulation risk.

### Strategy 1: Mandatory research before documentation

Require that agents read relevant sources before writing documents. An agent that has never consulted tool documentation should not be making claims about tool requirements.

This is expressible as AORTA obligations and prohibitions — see the worked example below.

### Strategy 2: Multi-agent verification workflows

Use dependency relations and reviewer obligations to ensure claims are checked by a separate agent. The reviewer role can be obligated to verify factual claims before the workflow proceeds.

### Strategy 3: Structured output where feasible

Where the output format permits it, require structured artifacts (YAML configs, JSON schemas) instead of prose. Structured fields like `requires: [git]` are at least enumerable and checkable against a known-facts database, even if the population of those fields is still LLM-driven.

### Strategy 4: Scope-limited claims

Constrain what each role is permitted to make claims about. An implementer scoped to `src/auth/` shouldn't be writing documentation about deployment requirements. Scope restrictions on Write already exist in the model; extending the concept from paths to topics is the natural next step (though topic detection itself requires NLU).

## The Honest Assessment

AORTA can make hallucination **less likely** and **more catchable**. It cannot **prevent** it.

The gap between "structurally more disciplined" and "deterministically prevented" is exactly the gap between governing actions and governing reasoning. AORTA lives on the action side. Hallucination lives on the reasoning side.

This is not a limitation unique to AORTA — it applies to any governance framework operating on tool-use boundaries. The contribution of AORTA is making the organizational discipline **formal, verifiable, and enforceable**, rather than advisory text in a system prompt that the LLM may ignore.

---

## Worked Example: Epistemic Discipline for Documentation

A concrete organizational specification that reduces hallucination risk by enforcing a research-before-writing workflow. This uses only mechanisms already present in AORTA: obligations, prohibitions, achievements, and dependency tracking.

### The problem

An architect agent is tasked with writing a design document. Without constraints, it may confidently assert requirements ("Tool X requires Feature Y") based on training data rather than actual verification.

### The organizational specification

```yaml
organization: epistemic_discipline_workflow

roles:
  researcher:
    objectives:
      - sources_gathered(Topic)
      - facts_verified(Topic)
    capabilities:
      - read_file
      - execute_command   # for checking tool versions, docs, etc.

  author:
    objectives:
      - document_written(Topic)
      - document_reviewed(Topic)
    capabilities:
      - read_file
      - write_file

  fact_checker:
    objectives:
      - claims_verified(Topic)
    capabilities:
      - read_file
      - execute_command

dependencies:
  # Author depends on researcher completing fact gathering
  - role: author
    depends_on: researcher
    for: facts_verified(Topic)

  # Fact checker depends on author producing a document
  - role: fact_checker
    depends_on: author
    for: document_written(Topic)

norms:
  # --- Researcher norms ---

  # Researcher is obliged to gather sources before facts can be verified
  - role: researcher
    type: obliged
    objective: sources_gathered(Topic)
    deadline: facts_verified(Topic)
    condition: task_assigned(Topic)

  # --- Author norms ---

  # Author must not write documentation until research is complete
  - role: author
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: is_documentation(Path), topic_of_doc(Path, Topic), not(achieved(facts_verified(Topic)))

  # Author must not write outside the docs/ directory
  - role: author
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: not(in_docs_dir(Path))

  # Author is obliged to produce a document once research is available
  - role: author
    type: obliged
    objective: document_written(Topic)
    deadline: phase_deadline(authoring)
    condition: facts_verified(Topic)

  # --- Fact-checker norms ---

  # Fact checker is obliged to verify claims after document is written
  - role: fact_checker
    type: obliged
    objective: claims_verified(Topic)
    deadline: phase_deadline(review)
    condition: document_written(Topic)

  # Fact checker must not modify source code or documentation
  - role: fact_checker
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: "true"   # cannot write anything

rules:
  - "is_documentation(Path) :- atom_concat('docs/', _, Path)."
  - "in_docs_dir(Path) :- atom_concat('docs/', _, Path)."
  - "task_assigned(T) :- achieved(task_assigned(T))."
  - "facts_verified(T) :- achieved(facts_verified(T))."
  - "document_written(T) :- achieved(document_written(T))."

  # Topic extraction from doc path — simplified convention:
  # docs/design_<topic>.md → topic is the filename stem
  - "topic_of_doc(Path, Topic) :- atom_concat('docs/', Rest, Path), atom_concat(Topic, '.md', Rest)."
```

### How the workflow executes

```
1. Orchestrator assigns task, achieves: task_assigned(health_check)

2. RESEARCHER phase
   - Obligation activates: sources_gathered(health_check)
   - Agent reads existing code, checks tool docs, runs version commands
   - Orchestrator achieves: sources_gathered(health_check), facts_verified(health_check)

3. AUTHOR phase
   - Prohibition was active: can't write docs/ until facts_verified(health_check) ✓
   - Now facts_verified is achieved → prohibition condition fails → write permitted
   - Agent writes docs/design_health_check.md
   - Orchestrator achieves: document_written(health_check)

4. FACT-CHECKER phase
   - Obligation activates: claims_verified(health_check)
   - Agent reads the document + original sources
   - Checks specific claims against tool output, file contents
   - Flags any claims not supported by gathered sources
   - Orchestrator achieves: claims_verified(health_check) (or records violation)
```

### What this prevents and what it doesn't

**Prevents (deterministically):**
- Author writing documentation before research is done (Prolog prohibition, enforced at tool-call boundary)
- Author writing files outside docs/ (path-based prohibition)
- Fact-checker modifying any files (blanket write prohibition)
- Skipping the fact-checking phase (obligation tracking, violation on deadline)

**Makes less likely (organizationally):**
- False claims about tool requirements — researcher phase forces the agent to actually check
- Unsupported assertions — fact-checker provides a second perspective
- Confident confabulation — the workflow structure creates friction before claims reach documents

**Cannot prevent:**
- An agent reading the right documentation and still drawing the wrong conclusion
- An agent hallucinating during the research phase itself (misreading tool output)
- Subtle errors that a fact-checker agent also misses

The deterministic guarantees apply to the workflow structure. The reduction in hallucination comes from the organizational discipline that structure enforces — the same way peer review in human organizations doesn't guarantee correctness but systematically reduces error rates.
