---
status: accepted
---

# Split Python runtime for application and ML workloads

The application targets Python 3.14, while vLLM and CUDA-oriented fine-tuning run in Python 3.12 containers or environments because their compatibility cadence differs. The network/model-provider seam prevents the specialized runtime from constraining the main application.
