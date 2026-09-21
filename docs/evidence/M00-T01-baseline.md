# M00-T01 — Workspace baseline

Captured: 2026-09-21 16:03 WEST  
Workspace: `/Users/ivanverenich/Documents/searching_job/rag_project`

## Purpose

This is the baseline captured before implementing the application. It distinguishes the curriculum documentation already present from files that the implementation tasks will create later.

## Operating system and hardware

| Item | Result |
|---|---|
| Operating system | macOS 26.6.2, build 25G83 |
| Kernel | Darwin 25.6.0 |
| Architecture | `arm64` |
| Machine | Apple M1 Pro, 10 CPU cores, 32 GB RAM |
| GPU | Integrated Apple M1 Pro GPU, 16 GPU cores, Metal-capable |

## Available tools

| Tool | Result | Notes |
|---|---|---|
| Python | 3.14.2 | Available as `python3` |
| uv | 0.11.28 | Homebrew aarch64 build |
| Docker CLI | 29.5.3 | Installed |
| Docker Compose | v5.1.4 | Installed |
| Docker daemon | Not verified | The session could not access `/Users/ivanverenich/.docker/run/docker.sock` |
| Git | 2.50.1 | Installed; this workspace is not yet a Git repository |
| kubectl | Client v1.34.1 | Kustomize v5.7.1; no cluster was checked |
| Terraform | Not installed | Planned for M13 |
| AWS CLI | Not installed | Planned for M13 |
| Ollama | Installed, version command failed | The binary aborted while initializing MLX/Metal in this restricted session; runtime health is not established |

## Current workspace

The workspace contains curriculum and design documentation only:

- root README and domain glossary;
- architecture, security, evaluation, operations, and reference-project notes;
- primary-source research;
- ADRs;
- 238 dependency-ordered curriculum task files and milestone indexes.

It does **not** yet contain:

- Python application source code;
- tests or a Python virtual environment;
- `pyproject.toml` or `uv.lock` for this project;
- Dockerfiles or application Docker Compose configuration;
- database migrations or application data;
- Git history or a `.git` directory;
- Confluence credentials, organization documents, or generated private data.

## Commands used

```text
pwd
uname -a
uname -m
sw_vers
python3 --version
uv --version
docker --version
docker compose version
git --version
kubectl version --client
terraform version
aws --version
ollama --version
rg --files .
git status --short --branch
docker info --format '{{.ServerVersion}}'
```

`terraform version` and `aws --version` reported that the commands were not found. `git status` reported that this directory is not a Git repository. `docker info` could not access the Docker socket. `ollama --version` aborted during native Metal/MLX initialization; that result is recorded rather than treated as a successful runtime check.

## Baseline conclusion

Baseline captured before implementation. The workspace contains curriculum documentation only. No application code, project dependencies, containers, external services, or Git history were created by this task.

The next task, M00-T02, may initialize Git and commit this baseline together with the first deliberate project checkpoint.
