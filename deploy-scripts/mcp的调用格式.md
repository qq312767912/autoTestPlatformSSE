以 WHartTest MCP 中真实存在的工具 `module_to_which_it_belongs(project_id: int)` 为例。

工具定义位于 [WHartTest_tools.py](/Users/chenxin42/Desktop/AI自动化测试平台_new/WHartTest-2.5.0/WHartTest_MCP/WHartTest_tools.py:155)。

## 1. WHartTest 发给通义代理

```json
{
  "model": "lingma-model",
  "messages": [
    {
      "role": "user",
      "content": "查询项目 ID 12 下的模块"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "module_to_which_it_belongs",
        "description": "根据WHartTest平台项目id去获取模块及id",
        "parameters": {
          "type": "object",
          "properties": {
            "project_id": {
              "type": "integer"
            }
          },
          "required": ["project_id"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "stream": false
}
```

## 2. 通义代理应返回给 WHartTest

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_001",
            "type": "function",
            "function": {
              "name": "module_to_which_it_belongs",
              "arguments": "{\"project_id\":12}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

这就是对方需要实现的关键响应格式。其中 `arguments` 必须是 JSON 字符串。

## 3. WHartTest 调用 MCP Server

平台解析 `tool_calls` 后，会通过 MCP Streamable HTTP 执行工具。对应的 MCP JSON-RPC 语义如下：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "module_to_which_it_belongs",
    "arguments": {
      "project_id": 12
    }
  }
}
```

MCP Server 返回结果类似：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "[{\"module_id\":101,\"module_name\":\"登录模块\"}]"
      }
    ],
    "isError": false
  }
}
```

## 4. WHartTest 把结果回传给通义代理

```json
{
  "model": "lingma-model",
  "messages": [
    {
      "role": "user",
      "content": "查询项目 ID 12 下的模块"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_001",
          "type": "function",
          "function": {
            "name": "module_to_which_it_belongs",
            "arguments": "{\"project_id\":12}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_001",
      "content": "[{\"module_id\":101,\"module_name\":\"登录模块\"}]"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "module_to_which_it_belongs",
        "description": "根据WHartTest平台项目id去获取模块及id",
        "parameters": {
          "type": "object",
          "properties": {
            "project_id": {
              "type": "integer"
            }
          },
          "required": ["project_id"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "stream": false
}
```

最后，通义模型正常返回文本：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "项目 ID 12 下包含登录模块，模块 ID 为 101。"
      },
      "finish_reason": "stop"
    }
  ]
}
```

对方代理只需要处理第 1、2、4 步；第 3 步由 WHartTest 平台完成。