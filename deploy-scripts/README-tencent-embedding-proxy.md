# Tencent LKEAP / ADP Embedding 代理

该代理不修改 WHartTest 后端，将现有 OpenAI 兼容请求转换为开发测试网 ADP GetEmbedding 的 TC3 签名请求。仅使用 Python 标准库。

运行环境：Linux，Python 3.7 及以上。

## 启动

    export ADP_HOST='<开发测试网网关主机:port>'
    export ADP_SECRET_ID='MOCK_CAPI_SECRET_ID_VALUE'
    export ADP_SECRET_KEY='MOCK_CAPI_SECRET_KEY_VALUE'
    export ADP_MODEL='sn-large-multi-language-v0.2.5'
    python3 deploy-scripts/tencent_embedding_proxy.py

也可用 ADP_ENDPOINT='http://host:port/atomic' 代替 ADP_HOST。默认监听 0.0.0.0:8920。

## 内网一键部署

解压部署包后：

    cp tencent-embedding-proxy.env.example proxy.env
    vi proxy.env
    sudo ./deploy-tencent-embedding-proxy.sh proxy.env

查看状态和日志：

    systemctl status tencent-embedding-proxy
    journalctl -u tencent-embedding-proxy -f

更新配置后重新执行部署脚本即可。没有 systemd 的容器或精简系统可使用：

    ./run-tencent-embedding-proxy.sh proxy.env

## WHartTest 配置

- 嵌入服务：自定义 API
- API 基础 URL：http://<代理主机>:8920/v1/embeddings
- API Key：留空（如设置了 PROXY_API_KEY，此处填相同值）
- 模型名称：sn-large-multi-language-v0.2.5（实际上游模型由 ADP_MODEL 固定）

如代理也在 Compose 网络中，API URL 应使用容器服务名，不要使用 127.0.0.1。

## 验证

    curl -fsS http://127.0.0.1:8920/health
    curl -fsS http://127.0.0.1:8920/v1/embeddings -H 'Content-Type: application/json' -d '{"model":"sn-large-multi-language-v0.2.5","input":["你好","遥控钥匙如何解锁车辆"]}'

代理会将超过 7 条的输入自动分批，并转换为 WHartTest 所需的 data[].embedding 响应格式。

如开发测试网使用官方文档的新模型，可设置 ADP_MODEL=lke-text-embedding-v2 和 ADP_TEXT_TYPE=document。此时代理发送 TextType；对 demo 的旧模型则发送 Online=false。
