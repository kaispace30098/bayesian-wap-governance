# Bayesian Write-Audit-Publish (WAP) Governance Architecture

A two-stage CI/CD validation architecture for high-volume, low-frequency administrative data systems in regulated environments.

![Architecture Overview](diagrams/architecture_overview.png)

## Overview

This repository contains the technical whitepaper describing a Bayesian data validation framework integrated into CI/CD pipelines. The architecture combines:

- **Stage 1:** O(N) sufficient statistics extraction via containerized pytest
- **Stage 2:** O(1)-per-metric Bayesian scoring using Normal-Inverse-Gamma conjugate model with Student-t posterior predictive
- **HITL Circuit Breaker:** Four-state override taxonomy (IGNORE_ANOMALY, REGIME_CHANGE, ACKNOWLEDGED_OUTLIER, TRANSITION_STATE) with full audit trail
- **Artifact-Injection Protocol:** Cross-environment relay bridging cloud CI agents and local Docker-hosted Streamlit auditor UI

## Paper

See [`paper/bayesian_wap_architecture.pdf`](paper/bayesian_wap_architecture.pdf)

## Key Design Decisions

- Log-ratio transform for symmetric YoY comparisons
- Hierarchical median pooling across 80+ metrics
- Adaptive κ₀ tiering by historical depth
- Deterministic materiality thresholds for interpretability
- Selective stage re-triggering to skip expensive O(N) recomputation

## Related Work

Companion research: [arXiv:2510.22419v2](https://arxiv.org/abs/2510.22419v2) — Constrained Joint Quantile Regression via Augmented Lagrangian Method (PyTorch)