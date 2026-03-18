# Checker Roadmap

Status: Proposed  
Last reviewed: 2026-03-18

## Objective

Build a checker roadmap that makes McKaySystem materially better at finding real
cloud savings, not just emitting more findings.

This roadmap is intentionally checker-centric. It focuses on how to make the
platform's resource analysis:

- more explicit
- more actionable
- less noisy
- less duplicative
- more explainable

It complements, rather than replaces, the broader platform backlog.

The roadmap assumes the existing non-negotiable constraints remain true:

- Postgres is the product source of truth
- all product reads stay scoped by `tenant_id` and `workspace`
- findings must remain deterministic and idempotent
- lifecycle semantics stay in the database layer
- checkers must be resilient to malformed or partial cloud data

---

## Why this roadmap matters

The platform already has a strong checker foundation:

- multiple AWS cost and governance domains are covered
- findings are deterministic and stable
- the worker, ingest, API, and frontend layers are clearly separated
- pricing and CUR enrichment paths already exist

But the next wave of value will not come from raw checker count alone. It will
come from stronger signals.

For cost checkers, "stronger" means:

- they point to a clear action
- they avoid overlapping or double-counted savings
- they explain why the recommendation exists
- they estimate impact conservatively and transparently
- they degrade gracefully when permissions or metrics are incomplete

---

## Roadmap principles

### 1. Savings quality beats checker count

A smaller number of trustworthy, high-confidence recommendations is better than
a larger number of weak or repetitive findings.

### 2. Strong checkers are explicit

The best savings checkers are often opinionated:

- "this bucket likely needs multipart cleanup"
- "this EFS throughput mode does not fit observed demand"
- "this ELB is deletable because it is both idle and has no registered targets"

Avoid vague phrasing when the evidence supports something stronger.

### 3. Duplicate savings must be suppressed

If two findings describe the same deletable cost surface, only one should own
the estimated savings.

Examples:

- idle ELB plus no registered targets
- grouped recommendation plus leaf findings
- resize recommendation plus schedule-stop recommendation on the same dev asset

### 4. Evidence must travel with the finding

Every strong cost signal should expose its evidence clearly:

- time window
- utilization shape
- configuration facts
- pricing source
- overlap or exclusion rules

### 5. Confidence is multidimensional

Cost checkers should not collapse certainty into one vague number alone.

We should distinguish:

- confidence that the issue exists
- confidence that the estimated savings is directionally right
- confidence that the action is safe

### 6. Better to miss a weak opportunity than overstate a strong one

False precision kills trust quickly. The roadmap should bias toward:

- directional but honest savings
- conservative thresholds
- explicit "review" findings where automatic savings ownership is too risky

---

## Checker maturity model

We should evolve checkers through four maturity levels.

### Level 1: Hygiene

Signals:

- missing encryption
- missing lifecycle configuration
- disabled backups
- public-access misconfiguration

Strengths:

- deterministic
- easy to explain

Weaknesses:

- often low direct savings precision

### Level 2: Resource waste detection

Signals:

- idle compute
- unattached volumes
- unused filesystems
- idle load balancers

Strengths:

- directly relevant to savings

Weaknesses:

- can be noisy without good suppression and usage windows

### Level 3: Configuration-fit optimization

Signals:

- throughput mode misfit
- storage-class transition opportunity
- gp2 to gp3 migration
- commitment coverage gap

Strengths:

- often high-value
- feels more product-grade

Weaknesses:

- needs stronger evidence and better wording

### Level 4: Decision-grade recommendation

Signals:

- concrete target state
- overlap-aware savings ownership
- actionability score
- rollback-aware or blast-radius-aware guidance

Strengths:

- closest to a world-class FinOps product surface

Weaknesses:

- requires better evidence, grouping, and recommendation logic

---

## Current checker assessment

The current checker set is already promising, but maturity varies by domain.

### Stronger current areas

- EC2 idle and Graviton-style optimization foundations
- EBS unattached and snapshot cleanup foundations
- ELBv2 hygiene and idle analysis
- S3 governance plus improved storage optimization signals
- EFS hygiene plus early throughput and lifecycle checks
- coverage and run-health work improving platform trust

### Current gaps

- some cost signals are still hygiene-heavy rather than decision-heavy
- several storage and network domains remain underdeveloped
- overlap suppression is still checker-local rather than platform-wide
- recommendation ownership and savings packaging are still immature
- some services expose opportunities but not yet the best next action

---

## Priority framework

Each roadmap item should be ranked by:

- savings potential
- signal strength
- implementation complexity
- risk of false positives
- dependency on broader platform work

Use these four buckets:

### P0: Immediate trust and savings quality

Work that improves recommendation quality right now.

### P1: High-ROI checker upgrades

Work likely to produce materially better savings coverage quickly.

### P2: Deeper service expansion

Work that broadens checker surface after quality foundations are stronger.

### P3: Recommendation-grade packaging

Work that depends on graph, overlap, actionability, and realized-savings loops.

---

## P0 roadmap

## 1. Overlap and duplicate suppression

Objective:

- stop double-counting savings across related cost findings

Why first:

- this improves trust immediately
- it strengthens every downstream recommendation layer

Key work:

- define per-service overlap rules
- assign one primary savings owner per duplicate cluster
- expose suppression semantics in recommendation logic later

Examples:

- ELB idle vs no registered targets
- grouped recommendation vs leaf findings
- no targets vs no healthy targets vs no listeners

Exit criteria:

- major same-resource double-counting cases are covered
- overlapping findings still exist when useful, but only one owns savings

## 2. Stronger evidence and dimensions

Objective:

- make high-value findings self-explanatory

Key work:

- standardize evidence dimensions by checker family
- include metric windows, thresholds, and observed values
- include explicit "why this matters" notes in cost findings

Examples:

- EC2 rightsizing:
  - CPU, memory, network, lookback windows
- S3:
  - storage mix, multipart age, lifecycle coverage
- EFS:
  - throughput mode, p95 PercentIOLimit, lifecycle transitions

Exit criteria:

- a user can understand a finding without reading source code or guessing logic

## 3. Confidence model v1 for checker outputs

Objective:

- move from implicit confidence to an explicit, explainable model

Key work:

- separate:
  - issue confidence
  - savings confidence
  - action safety confidence
- define deterministic mapping rules from data quality and heuristic coverage

Exit criteria:

- major cost findings consistently communicate confidence inputs

---

## P1 roadmap

## 1. Storage optimization wave

This is one of the highest-value areas because storage waste is common,
persistent, and often poorly detected by simpler tools.

### 1.1 S3

Goals:

- move from governance plus directional storage estimates to explicit storage
  actions

Roadmap items:

- stronger transition candidate scoring:
  - distinguish IA vs Intelligent-Tiering vs Glacier-style recommendations
- multipart cleanup materiality:
  - estimate likely waste more explicitly where possible
- replication anomaly detection:
  - differentiate expected DR replication from wasteful broad replication
- tiering suggestions:
  - use storage mix, lifecycle coverage, and access uncertainty more clearly
- overlap suppression:
  - avoid double-counting transition, tiering, and replication recommendations

Desired end state:

- S3 becomes one of the strongest storage checkers in the platform

### 1.2 EBS

Goals:

- make EBS recommendations more direct and more complete

Roadmap items:

- stronger gp2 -> gp3 conversion analysis
- provisioned IOPS / throughput mismatch
- snapshot retention and age materiality
- relationship-aware suppression with instance lineage and graph context later

Desired end state:

- one clear primary recommendation per waste surface:
  - delete
  - convert
  - rightsize
  - retain intentionally

### 1.3 EFS / FSx

Goals:

- make managed filesystem checkers more explicit about storage tier and
  throughput economics

Roadmap items:

- EFS:
  - stronger elastic vs bursting vs provisioned throughput fit
  - IA vs Archive opportunity scoring
  - distinguish hygiene-only lifecycle gaps from stronger storage savings cases
- FSx:
  - throughput/storage mismatch
  - low-utilization but expensive throughput configuration
  - lifecycle and data-class opportunities where service supports them

Desired end state:

- filesystem checkers surface clear operational fit issues, not only generic
  lifecycle warnings

## 2. Network and ingress cost wave

### 2.1 ELB / CloudFront / NAT

Goals:

- make networking findings more deletable, more attributable, and less noisy

Roadmap items:

- ELB:
  - stronger deletable-state heuristics
  - better distinction between low traffic and no-value traffic
- NAT:
  - better attribution of high data-processing paths
  - route and dependency-aware packaging later
- CloudFront:
  - low-value distributions
  - poor cache fit
  - misfit origin or traffic patterns

Desired end state:

- networking findings feel operationally safe and financially meaningful

## 3. Rightsizing and schedule-aware compute wave

### 3.1 EC2 / RDS / containers

Goals:

- move beyond basic underutilization thresholds into safer target-state
  recommendations

Roadmap items:

- multi-window rightsizing:
  - p50, p95, peak
- schedule-aware stop/start recommendations:
  - dev/test and office-hours workloads
- better memory and throughput-aware sizing
- safer action guidance and rollback notes

Desired end state:

- compute recommendations say what to do, not only that something is oversized

---

## P2 roadmap

## 1. Service coverage expansion

Once P0 and P1 quality foundations are stronger, expand high-value checker
coverage into missing domains.

Top candidates:

- DynamoDB:
  - provisioned capacity
  - table class
  - unused GSIs
- Redshift:
  - pause opportunities
  - resize
  - idle clusters
- OpenSearch:
  - oversized data nodes
  - storage-tier opportunities
- ElastiCache:
  - low-utilization nodes
  - engine family modernization
- Route 53 / Global Accelerator / data transfer:
  - expensive low-value network services
- security telemetry cost governance:
  - duplicate or excessive Config, CloudTrail, GuardDuty patterns

Expansion rule:

- only add new services when the checker can be at least Level 2 or Level 3
  quality quickly

---

## P3 roadmap

## 1. Graph-aware recommendation packaging

This phase depends on the resource relationship graph and overlap model.

Goals:

- package related findings into one stronger recommendation
- improve owner hints and blast-radius context
- reduce fragmented surfaces

Examples:

- ELB + target groups + compute dependency package
- NAT + route path package
- EBS volume + terminated instance lineage package

## 2. Actionability scoring

Goals:

- rank recommendations by a blend of:
  - estimated savings
  - confidence
  - ease of implementation
  - reversibility
  - ownership clarity

## 3. Realized savings feedback loop

Goals:

- compare predicted vs realized savings after remediation
- improve checker tuning over time
- maintain a precision / false-positive feedback surface

---

## Proposed execution phases

## Phase 1: Trust and explicitness

Build:

- overlap suppression rules for top duplicate cases
- evidence-rich dimensions for major cost checkers
- confidence model v1 for checker outputs

Exit criteria:

- key cost findings are more explainable and less duplicative

## Phase 2: Storage and network checker strengthening

Build:

- deeper S3 optimization wave
- deeper EBS optimization wave
- EFS and FSx throughput / lifecycle fit improvements
- stronger ELB / NAT / CloudFront cost signals

Exit criteria:

- storage and network become product-strength savings surfaces

## Phase 3: Compute and schedule intelligence

Build:

- better rightsizing
- schedule-aware waste detection
- safer action guidance

Exit criteria:

- major compute services produce clearer target-state recommendations

## Phase 4: Expansion and packaging

Build:

- new service domains
- graph-aware recommendation packaging
- actionability scoring
- realized savings loop

Exit criteria:

- recommendations feel cohesive and portfolio-aware, not checker-by-checker

---

## Recommended first 10 implementation slices

If we want a practical execution order, I would start here:

1. Formalize overlap suppression rules for top duplicate cost findings.
2. Standardize evidence dimensions for S3, ELB, EFS, EBS, and EC2.
3. Add checker confidence model v1.
4. Strengthen S3 transition candidate scoring.
5. Add stronger EBS gp2 -> gp3 and throughput-fit analysis.
6. Strengthen EFS throughput-mode fit beyond current provisioned-only logic.
7. Improve NAT and ELB deletable-state and attribution heuristics.
8. Add schedule-aware compute and database waste detection.
9. Expand into DynamoDB or Redshift with decision-grade scope only.
10. Introduce graph-backed recommendation packaging for a small set of services.

---

## Acceptance criteria for the roadmap

This roadmap is successful when it helps the team consistently choose checker
work that:

- improves real savings detection, not just issue count
- reduces duplicate and overlapping savings ownership
- increases explainability and operator trust
- fits the platform's deterministic and multi-tenant constraints
- creates a clear path from finding -> recommendation -> remediation -> realized
  savings

---

## Relationship to other plans

This checker roadmap should be read alongside:

- `docs/07_improvements/platform_improvement_backlog.md`
- `docs/07_improvements/coverage_visibility_phase2_plan.md`
- `docs/07_improvements/resource_relationship_graph_implementation_plan.md`

The backlog remains the broad strategic view.

This roadmap is the more focused execution plan for building stronger cost and
optimization checkers.
