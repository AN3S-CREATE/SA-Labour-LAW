# .index/ — persistent project context

This folder is the durable map of **what this code is**. It is read at the start
of substantive work and updated whenever files, architecture or decisions
change.

## Files

| File | Purpose | Update trigger |
|---|---|---|
| `README.md` | This guide | Structural or process change |
| `file-inventory.md` | Every file: path, purpose, key symbols, status | Any create / edit / move / delete |
| `architecture.md` | Components, data flow, patterns, constraints | Meaningful architectural change |
| `key-decisions.md` | ADR-style log of technical and policy decisions | A new significant decision |
| `dead-code.md` | Dead or unused code and tech debt, with evidence | Discovery of either |
| `context-refresh-log.md` | Record of index refreshes | After every meaningful refresh |

## Reading protocol

Load `file-inventory.md` and `architecture.md` first. Load `key-decisions.md`
before changing collection policy, source handling or the ontology — this
project has policy decisions that are easy to undo by accident. Load
`dead-code.md` only for refactoring or tech-debt work.

## Project in one line

`sa_labour_corpus/` is a local-first harvester that collects publicly available
South African labour-law instruments and case law into a provenance-tracked
on-disk corpus, with an unfair-dismissal ontology and a mining overlay.

## The one thing to know before changing anything

Several sources are **deliberately not collected**, and that is a policy choice
recorded in `key-decisions.md`, not an unfinished feature. Before "fixing" the
SAFLII or CCMA adapters so they return data, read decisions D-001 and D-002.
