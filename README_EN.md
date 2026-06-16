# WHartTest - AI-Powered Intelligent Test Case Generation Platform

English | [中文](README.md)

## Overview

WHartTest is an AI-powered intelligent testing platform built on **Django 5.2 + DRF** and modern LLM technologies. The platform adopts a frontend-backend separated Monorepo architecture, consisting of 6 sub-projects (Django backend, Vue frontend, UI automation executor, MCP tool service, Agent skill library, and online document editor). By aggregating natural language understanding, knowledge base retrieval, and embedding search capabilities, combined with **LangChain/LangGraph** and **MCP (Model Context Protocol)** tool calls, it achieves automated generation, management, and execution of test cases from requirements to executable test cases, providing a complete intelligent testing management solution for QA teams.

## Documentation
For full documentation, visit: https://docs.wharttest.mgdaas.cn:4430/

## Quick Start

### Docker Deployment (Recommended - out of the box)

```bash
# 1. Clone the repo
git clone https://github.com/MGdaasLab/WHartTest.git
cd WHartTest

# 2. Prepare config (use defaults with auto-generated API Key)
cp .env.example .env

# 3. One-command start (choose one of the two)
# Option A: use the deployment script (recommended, auto-selects registry mirrors)
./run_compose.sh

# Option B: use docker-compose directly
docker-compose up -d

# 4. Open the system
# http://localhost:8913 (admin/admin123456)
```

**That's it!** The system will automatically create a default API Key and enable MCP services out of the box.

### Unified Deployment Script

If you use the built-in deployment script, it now asks you to choose between **remote image pull** and **local image build** at startup:

```bash
./run_compose.sh
```



> ⚠️ **Production note**: Log in to the admin panel, delete the default API Key, and create a new secure key. See: [Quick Start Guide](./docs/QUICK_START.md)

Detailed deployment docs:
- [Quick Start Guide](./docs/QUICK_START.md) - **recommended for new users**
- [GitHub Auto-Build Deployment Guide](./docs/github-docker-deployment.md)
- [Full deployment docs](https://mgdaaslab.github.io/WHartTest/)



## Contact

For questions or suggestions:
- Open an Issue
- Use the project Discussions
- When adding WeChat, mention `github` so we can add you to the group chat.
- Join the group to get the latest updates and Skills.

<img width="400" alt="image" src="docs/public/img/wx.jpg" />

QQ groups:
1. 8xxxxxxxx0 (full)
2. 1017708746

---

## IMPORTANT SECURITY NOTICE: Skills Permissions and Deployment Safety (v1.4.0 and later)
Because the Skills module has high system execution privileges, please take the following security precautions:

Deployment recommendation: Only deploy in an intranet or trusted private network.
Access control: Do not expose the service to the public Internet or grant access to unauthenticated or untrusted users.
Disclaimer: This project (WHartTest) is for learning and research purposes only. Users are responsible for all security risks and consequences caused by unsafe deployment (such as public exposure or missing authentication). The WHartTest team is not liable for any security incidents, including data leaks or server compromise, caused by improper configuration.

**WHartTest** - AI-powered test case generation that makes testing smarter and development more efficient!
