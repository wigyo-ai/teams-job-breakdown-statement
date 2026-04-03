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
| [Deployment Guide](./docs/DEPLOYMENT_GUIDE.md) | Step-by-step instructions to deploy on Azure Container Apps |
| [Configuration Reference](./docs/CONFIGURATION_REFERENCE.md) | All environment variables and config options |

---

## Quick Summary

| Layer | Technology |
|---|---|
| Conversational AI + RAG | H2O Enterprise h2oGPTe |
| Knowledge Base Ingestion | H2O Document AI + SharePoint (Microsoft Graph API) |
| Messaging Interface | Microsoft Teams (Azure Bot Service) |
| Orchestration & State | Python FastAPI + in-memory session store (single worker) |
| Conversation History | h2oGPTe called fresh per section (conversation_id=None each call) |
| Admin Dashboard | H2O Wave — deployed via HAIC App Store (not Helm) |
| Document Generation | python-docx (Azure Container Apps) |
| Object Storage | Azure Blob Storage |
| Deployment Platform | Azure Container Apps (ACA) |
| Monitoring | H2O MLOps |

---

## High-Level Flow

```
User (Microsoft Teams)
        │
        ▼
  Webhook Handler (FastAPI on Azure Container Apps)
  — JWT Bearer token validated (Azure Bot Service)
        │
        ▼
  Conversation Orchestrator (in-memory session state, --workers 1)
        │
        ├─ Phase 1 (Setup — code only, no LLM)
        │    Collects 4 fields: Customer Name → Site Name → Site Category → Job Purpose
        │
        └─ Phase 2 (Interview — hybrid LLM+RAG)
             Code drives state machine; h2oGPTe called once per section for suggestions
             suggest_duties → confirm_duties → suggest/confirm_tasks → suggest/confirm_safety
             → review → APPROVE
        │
        ▼
  h2oGPTe API ◄──── SharePoint RAG Collections (fresh RAG query per section)
        │                  (via Microsoft Graph API)
        │
        ▼
  On APPROVE: JSON Output ──► python-docx ──► .docx document ──► Azure Blob Storage
        │
        ▼
  Reply to User (Microsoft Teams — Bot Framework REST API)
```
