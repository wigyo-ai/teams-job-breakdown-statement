# Certis Security — Job Breakdown Statement (JBS) Automation Platform

## Overview

An AI-powered, conversational agent that interviews security operations personnel via **Microsoft Teams**, captures all requirements for a Job Breakdown Statement (JBS), and generates a compliant Word (.docx) document — fully grounded in SharePoint knowledge base content.

Built on the **H2O.ai Enterprise Platform**.

---

## Documentation Index

| Document | Purpose |
|---|---|
| [Solution Architecture](./docs/SOLUTION_ARCHITECTURE.md) | Platform design, component map, data flows |
| [Technical Design](./docs/TECHNICAL_DESIGN.md) | Component specs, APIs, code structure, integration details |
| [Deployment Guide](./docs/DEPLOYMENT_GUIDE.md) | Step-by-step instructions to deploy on H2O AI Cloud |
| [Configuration Reference](./docs/CONFIGURATION_REFERENCE.md) | All environment variables and config options |

---

## Quick Summary

| Layer | Technology |
|---|---|
| Conversational AI + RAG | H2O Enterprise h2oGPTe |
| Knowledge Base Ingestion | H2O Document AI + SharePoint (Microsoft Graph API) |
| Messaging Interface | Microsoft Teams (Azure Bot Service) |
| Orchestration & State | Python FastAPI + SQLite session store (no Redis pod) |
| Conversation History | H2O Enterprise h2oGPTe (native, via conversation_id) |
| Admin Dashboard | H2O Wave — deployed via HAIC App Store (not Helm) |
| Document Generation | python-docx (HAIC Kubernetes) |
| Deployment Platform | H2O AI Cloud (HAIC) — Kubernetes (Helm) + App Store |
| Monitoring | H2O MLOps |

---

## High-Level Flow

```
User (Microsoft Teams)
        │
        ▼
  Webhook Handler (FastAPI on HAIC)
  — JWT Bearer token validated (Azure Bot Service)
        │
        ▼
  Conversation Orchestrator (phase state in SQLite)
        │  Phases 1–5
        ▼
  h2oGPTe API ◄──── SharePoint RAG Collections
        │  (h2oGPTe stores full turn history natively via conversation_id)
        │                  (via Microsoft Graph API)
        │
        ▼
  Phase 4: Mozart API ── Reference Document Lookup
        │
        ▼
  Phase 5: JSON Output ──► python-docx ──► .docx document
        │
        ▼
  Reply to User (Microsoft Teams — Bot Framework REST API)
```
