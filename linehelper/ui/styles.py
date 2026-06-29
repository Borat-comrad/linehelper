"""CSS used by the Streamlit demo interface."""

from __future__ import annotations


APP_CSS = """
<style>
:root {
  --lh-bg: #f5f7fb;
  --lh-surface: #ffffff;
  --lh-surface-soft: #f8fafc;
  --lh-border: #d9e2ec;
  --lh-border-strong: #b7c7d8;
  --lh-text: #17202a;
  --lh-muted: #64748b;
  --lh-accent: #2563eb;
  --lh-accent-soft: #e8f0ff;
  --lh-green: #0f766e;
  --lh-green-soft: #e7f5f3;
  --lh-amber: #a16207;
  --lh-amber-soft: #fff7df;
  --lh-blue-soft: #edf4ff;
  --lh-shadow: 0 12px 28px rgba(15, 23, 42, 0.07);
}

.stApp {
  background: var(--lh-bg);
  color: var(--lh-text);
}

.block-container {
  padding-top: 2rem;
  max-width: 1180px;
}

h1, h2, h3 {
  letter-spacing: 0;
}

[data-testid="stSidebar"] {
  background: #ffffff;
  border-right: 1px solid var(--lh-border);
}

[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  color: var(--lh-text);
}

.lh-hero {
  background: linear-gradient(135deg, #ffffff 0%, #f4f8ff 100%);
  border: 1px solid var(--lh-border);
  border-radius: 8px;
  box-shadow: var(--lh-shadow);
  padding: 26px 28px 24px;
  margin-bottom: 18px;
}

.lh-hero h1 {
  font-size: 2rem;
  line-height: 1.2;
  margin: 0 0 8px;
  color: #111827;
}

.lh-hero p {
  margin: 0 0 16px;
  max-width: 760px;
  color: var(--lh-muted);
  font-size: 1rem;
}

.lh-badge-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.lh-badge {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--lh-border);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 0.78rem;
  font-weight: 650;
  color: var(--lh-muted);
  background: #ffffff;
  white-space: nowrap;
}

.lh-badge-semantic {
  background: var(--lh-blue-soft);
  border-color: #c7dbff;
  color: #1d4ed8;
}

.lh-badge-episodic {
  background: var(--lh-green-soft);
  border-color: #b9dfd9;
  color: var(--lh-green);
}

.lh-badge-onec {
  background: var(--lh-amber-soft);
  border-color: #ead48a;
  color: var(--lh-amber);
}

.lh-badge-local,
.lh-badge-neutral {
  background: #f3f6fa;
  color: #475569;
}

.lh-section-title {
  margin: 14px 0 8px;
  font-size: 0.92rem;
  font-weight: 750;
  color: #334155;
}

.lh-panel {
  background: var(--lh-surface);
  border: 1px solid var(--lh-border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 14px;
}

.lh-muted {
  color: var(--lh-muted);
}

.lh-hint {
  color: var(--lh-muted);
  font-size: 0.9rem;
  margin-top: 4px;
}

.stChatMessage {
  background: var(--lh-surface);
  border: 1px solid var(--lh-border);
  border-radius: 8px;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
  padding: 0.6rem 0.8rem;
}

.stChatMessage [data-testid="stMarkdownContainer"] p:last-child {
  margin-bottom: 0;
}

.lh-source-card {
  background: var(--lh-surface);
  border: 1px solid var(--lh-border);
  border-left-width: 4px;
  border-radius: 8px;
  padding: 14px 15px;
  margin: 10px 0;
}

.lh-source-semantic {
  border-left-color: #3b82f6;
}

.lh-source-episodic {
  border-left-color: #14b8a6;
}

.lh-source-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.lh-source-title {
  color: #111827;
  font-weight: 750;
  line-height: 1.35;
}

.lh-source-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 14px;
  color: var(--lh-muted);
  font-size: 0.82rem;
  margin-bottom: 6px;
}

.lh-source-meta--wide {
  gap: 7px 18px;
}

.lh-source-excerpt {
  background: var(--lh-surface-soft);
  border: 1px solid #e5edf5;
  border-radius: 6px;
  color: #334155;
  line-height: 1.5;
  margin-top: 10px;
  padding: 10px 11px;
}

.lh-sidebar-card {
  background: #f8fafc;
  border: 1px solid var(--lh-border);
  border-radius: 8px;
  padding: 12px;
  margin: 8px 0 14px;
}

.lh-status-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #e8eef5;
  padding: 7px 0;
  color: #334155;
  font-size: 0.9rem;
}

.lh-status-line:last-child {
  border-bottom: 0;
}

.lh-status-ok {
  color: #047857;
  font-weight: 750;
}

.lh-status-warn {
  color: #b45309;
  font-weight: 750;
}

button[kind="secondary"] {
  border-color: var(--lh-border-strong);
}
</style>
"""
