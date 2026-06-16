# WHartTest - AI驱动的智能测试用例生成平台

中文 | [English](README_EN.md)

## 项目简介

WHartTest 是基于 **Django 5.2 + DRF** 与现代大模型技术打造的 **AI 驱动智能测试平台**。平台采用前后端分离的 Monorepo 架构，由 6 个子项目组成（Django 后端、Vue 前端、UI 自动化执行器、MCP 工具服务、Agent 技能库、在线文档编辑器），聚合自然语言理解、知识库检索与嵌入搜索能力，结合 **LangChain/LangGraph** 与 **MCP（Model Context Protocol）** 工具调用，实现从需求到可执行测试用例的自动化生成、管理与执行，为测试团队提供完整的智能测试管理解决方案。

## 文档
详细文档请访问：https://docs.wharttest.mgdaas.cn:4430/

## 快速开始

### Docker 部署（推荐 - 开箱即用）

```bash
# 1. 克隆仓库
git clone https://github.com/MGdaasLab/WHartTest.git
cd WHartTest

# 2. 准备配置（使用默认配置，包含自动生成的API Key）
cp .env.example .env

# 3. 一键启动（以下两种方式二选一）
# 方式一：使用部署脚本（推荐，支持镜像源择优）
./run_compose.sh

# 方式二：直接使用 docker-compose
docker-compose up -d

# 4. 访问系统
# http://localhost:8913 (admin/admin123456)
```

**就这么简单！** 系统会自动创建默认API Key，MCP服务开箱即用。

### 统一部署脚本

如果你使用仓库自带脚本部署，现在启动后会先让你在“远程拉镜像”和“本地构建镜像”之间二选一：

```bash
./run_compose.sh
```


> ⚠️ **生产环境提示**：请登录后台删除默认API Key并创建新的安全密钥。


## 联系方式

如有问题或建议，请通过以下方式联系：
- 提交 Issue
- 项目讨论区
- 添加微信时请备注   github   ，拉你进微信群聊。
- 加群获取最新更新信息及SKill。

<img width="400" alt="image" src="docs/public/img/wx.jpg" />

qq群：
1. 8xxxxxxxx0（已满）
2. 1017708746
---
## 【重要安全警示】关于 v1.4.0 以及后续版本 Skills 权限及部署安全的声明
鉴于 Skills 模块具备较高的系统执行权限，为了保障您的数据与环境安全，我们做出以下严正提示：

部署建议：强烈建议仅在内网环境或受信任的私有网络中部署使用。 访问控制：切勿将服务直接暴露于公网（Public Internet），或授予任何未经身份验证及不可信人员访问权限。 免责声明：本项目（WHartTest）仅供学习与研究使用。用户需自行承担因违规部署（如开放公网、未做鉴权等）所导致的一切安全风险与后果。对于因不当配置引发的数据泄露、服务器被入侵等安全事故，WHartTest 团队不承担任何法律及连带责任。
**WHartTest** - AI驱动测试用例生成，让测试更智能，让开发更高效！
