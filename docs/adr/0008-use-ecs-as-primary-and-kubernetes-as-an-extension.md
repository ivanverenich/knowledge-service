---
status: accepted
---

# Use ECS as the primary AWS runtime and Kubernetes as an extension

Terraform provisions a complete ephemeral ECS/Fargate deployment before Kubernetes is introduced. A Helm deployment to `kind` and optionally EKS then teaches orchestration without making Kubernetes a prerequisite for understanding or operating the application.
