# Architecture

## Current portfolio prototype

```text
Browser
  │
  ├─ index.html
  ├─ Leaflet map renderer
  ├─ CARTO / OpenStreetMap basemap
  └─ Curated demonstration intelligence
```

## Intended production architecture

```text
User
  │
  ▼
Web UI / Chat Interface
  │
  ▼
Constraint Router
  │
  ├─ Branch / Base Case
  ├─ Transformer
  ├─ Bus / Breaker
  ├─ Nomogram / Interface
  └─ Special / OMS resolver
  │
  ▼
Entity + Topology Resolution
  │
  ├─ CAISO OASIS
  ├─ Full Network Model
  ├─ Transmission planning material
  ├─ Outage data
  └─ Curated geospatial registry
  │
  ▼
Grid Analytics
  │
  ├─ Source / sink classification
  ├─ PTDF
  ├─ LODF
  ├─ Pre/post-contingency headroom
  └─ Driver ranking
  │
  ▼
AI Explanation Layer
  │
  ▼
Structured Map JSON
  │
  ▼
Interactive CAISO Map
```

## Design principle

The LLM should **explain and synthesize**; deterministic code and solved network data should establish physical identities, topology, ratings, sensitivities, and interval-specific facts.

## Public vs private

### Public
- Front-end experience
- Example cases
- High-level architecture
- Screenshots
- Non-sensitive documentation

### Private
- Premium data credentials
- Constraint/entity resolver internals
- Proprietary source/sink scoring
- PTDF/LODF implementation and solved cases
- Proprietary ranking methodology
- LLM system prompts / evaluation data
