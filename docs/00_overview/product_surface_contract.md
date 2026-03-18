# Product Surface Contract

Status: Canonical  
Last reviewed: 2026-03-18

## Purpose

This document defines the product contract between the platform's main
customer-facing surfaces:

- findings
- recommendation candidates
- recommendations
- potential savings
- realized savings
- coverage

The goal is to prevent the platform from showing the same underlying signal as
multiple incompatible concepts.

---

## Core rule

The platform has two distinct layers:

1. **Detection layer**
   - factual
   - deterministic
   - resource-centric
   - run-derived
2. **Action layer**
   - curated
   - deduplicated when possible
   - workflow-oriented
   - package- and owner-aware

Findings belong to the detection layer.  
Recommendations belong to the action layer.

If the action layer adds no value beyond the finding, it should not create a
second product object.

---

## Definitions

### Findings

Findings answer:

- what was detected
- what evidence supports it
- how severe it is
- what the checker-estimated impact is

Findings may include checker advice text, but that advice remains explanatory.

Findings are:

- deterministic
- fingerprinted
- run-scoped
- resource-centric
- not deduplicated across overlapping savings logic by default

Findings are the authoritative detection record.

### Recommendation candidates

Recommendation candidates answer:

- which open findings currently match recommendation eligibility rules

They are:

- a pipeline metric
- useful for KPI reporting and recommendation coverage
- not the same as final recommendations

They may still overlap, because they are often one-per-finding.

### Recommendations

Recommendations answer:

- what should be done next
- how to act safely
- who should likely own the action
- whether multiple findings/resources belong to one action package

Recommendations must add value beyond findings through one or more of:

- deduplication
- grouping / packaging
- suppression
- owner hints
- actionability and approval semantics
- package-level savings ownership

Recommendations are the authoritative action layer.

### Potential savings

Potential savings answers:

- what could likely be saved if the primary actionable opportunities were
  implemented

Potential savings must come from the action layer, not directly from raw
findings.

It should be:

- deduplicated
- suppression-aware
- package-owner-aware

### Realized savings

Realized savings answers:

- what savings have actually been verified after remediation activity

It belongs to the remediation outcome layer and must remain separate from
potential savings.

### Coverage

Coverage answers:

- what was assessed
- what was not assessed
- where permissions or degraded runs limit trust

Coverage is not a savings layer and must not be mixed into savings KPIs.

---

## What each page should mean

### Findings page

Primary role:

- browse and investigate detected issues

Should emphasize:

- evidence
- severity
- checker advice
- lifecycle state

Should not imply:

- that every finding is already a final action object

### Recommendations page

Primary role:

- browse curated action objects

Should emphasize:

- action type
- target/current state
- package context
- ownership
- confidence and action safety
- deduplicated savings semantics

Should not behave as:

- a second findings table with the same rows and wording

### KPI dashboard

Primary role:

- explain value and trust through separate KPI families

Should distinguish:

- detected savings
- recommendation candidates
- potential savings
- realized savings
- coverage health

It must not collapse these into one misleading total.

### Coverage page

Primary role:

- explain assessment completeness and permission gaps

Permission-gap and access-denied signals belong here, not in recommendations.

---

## Canonical savings semantics

### Detected savings

Source:

- raw open findings with estimated savings

Use:

- engineering investigation
- internal detection breadth

Risk:

- may overstate reality because findings can overlap

### Potential savings

Source:

- deduplicated recommendations or package owners

Use:

- customer-facing value KPI
- prioritization

### Realized savings

Source:

- remediation outcome tracking and verification

Use:

- proof of value
- success reporting

---

## Platform rules

1. A finding may exist without a recommendation.
2. A recommendation must be derived from one or more findings.
3. Recommendation candidates are not final recommendations.
4. Potential savings must not be computed as a blind sum of findings.
5. Coverage gaps and permission issues must not be presented as recommendations.
6. If a recommendation adds no value beyond a finding, it should not exist as a
   separate object.

---

## Current compatibility note

Some current APIs and internal names still use `recommendations` for what is
more precisely `recommendation candidates`.

Where backward compatibility matters:

- keep the wire shape stable
- clarify the semantics in labels, docs, and definitions
- evolve naming carefully rather than silently changing product meaning
