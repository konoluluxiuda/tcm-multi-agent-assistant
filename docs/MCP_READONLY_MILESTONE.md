# 只读资料工具的MCP互操作

2026-10-27。先提交既有执行恢复与记忆增量：`36851ca`。本轮随后实现可选MCP入口，复用药典字段与ETCM属性查询，并接入现有报告图，不引入新的Agent编排框架。

## 实际能力与调用链

聊天页“本地研究”提交单药报告 → 原后台队列 → 原LangGraph两个证据分支 → `report_branch`按部署配置选择一种传输：

- `direct`：共享参数契约 → 原本地只读查询。
- `mcp`：应用适配器 → 独立Python运行SDK客户端 → stdio MCP Server → 同一共享契约 → 原本地只读查询。

两条路径互斥；MCP失败不暗中重试直接查询。两药报告的两个药材子报告也复用该入口，但共同靶点分支仍为原本地调用，尚未发布为MCP工具。

开放工具：

| 工具 | 参数 | 结果 |
|---|---|---|
| `lookup_pharmacopoeia` | `herb`（1–80字）、`question`（1–400字） | 原药典命中、字段、来源及查询状态 |
| `lookup_etcm_profile` | `herb`（1–80字） | 原ETCM属性、来源定位和身份未核验标记 |

参数禁止额外字段、空白和隐式类型转换；客户端不能传数据库路径。工具返回的状态区分参数错误、未知工具、超时、不可用及无匹配资料。出处保留；语义不变：来源关联不代表机制或药效。

## 架构选择

采用官方Python SDK **1.28.0的v1维护线**，不是声称采用最新主版本。其依赖要求高于主应用当前Pydantic 2.10.6，因此SDK在独立环境运行，主FastAPI/LangGraph依赖未升级。依赖依据见[该版本官方声明](https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/pyproject.toml)，stdio会话用法参考[官方版本示例](https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/clients/simple-chatbot/mcp_simple_chatbot/main.py)。固定已验收版本，后续升级需重新做兼容验收。

只用stdio，不开放HTTP/SSE监听端口。服务器没有模型sampling、记忆读取、写入、任意命令执行工具。应用启动子进程只传本地来源配置及必要运行环境，不传聊天密钥、会话库或PostgreSQL凭证。读取工作进程沿用禁网与只读数据库路径。

SDK客户端每次先初始化并发现工具，核对工具名称及Schema后调用。响应核对工具名、错误标志和结构化内容。stdout只用于协议/结果，日志走stderr。现有UI和JSON报告标注`tool_transport`，可验证确实经过MCP。

超时：本地工具45秒，SDK请求48秒，桥接55秒；不重试。应用取消时TERM桥接进程，桥接取消SDK会话并关闭其服务器进程树，超时再强杀桥接；Linux实际验证，Windows进程树未验收。取消是停止只读执行，不承诺外部副作用恰好执行一次。

每次工具调用创建专用会话，便于隔离和清理，但会增加启动与发现开销；本轮没有证明高并发性能或更高答案质量。后台队列仍是原调度器，MCP不是任务队列，也不是自主多Agent。

## 验收

- **真实数据与双客户端**：官方SDK桥接客户端、独立标准库JSON-RPC stdio探针，都完成初始化、工具发现、调用。药典当归贮藏与ETCM当归属性，归一化耗时字段后与直接调用数据及出处完全一致。第二客户端是本项目独立实现的协议探针，不是已集成第三方桌面产品。
- **协议故障注入**：未知工具、非法参数/路径字段、隐藏内部错误、显式`notifications/cancelled`取消长调用后继续ping、SDK短超时后关闭会话通过。故障注入服务器仅在测试模块中使用。
- **页面**：三种真实当归问法的字段、顺序、出处和历史回看通过，且药典/ETCM分支均断言`tool_transport=mcp`。
- **隔离回归**：共享契约、传输互斥、不做隐式fallback、取消桥接、单分支失败保留另一分支、单药/两药报告、后台API/恢复及部署限制。最新数字以审计回执为准。
- 首轮真实协议验收发现配置项编辑误改了`getenv`调用参数，导致原本地分支失败，已修复并重测。受限沙箱内SDK握手超时，允许本地进程通信的验收环境通过；未把沙箱失败当作协议成功。

本轮没有调用外部模型，不上传药典/ETCM。既有研究资料均留在本机。

## 启动

创建**独立**SDK环境，避免把依赖安装进主应用：

```bash
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-mcp.txt
```

`constraints-mcp.txt`记录本机Python 3.13/Linux验收解析结果；其他平台须重新验证，不宣称全平台兼容。

本机已准备好`/tmp/tcm-mcp-20261027/bin/python`，预览命令：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7888 --state var/mcp_readonly_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --mcp-python /tmp/tcm-mcp-20261027/bin/python
```

访问 http://127.0.0.1:7888/ ，展开“本地研究”，选择“药典 + ETCM报告”，输入当归、贮藏。

其他本地MCP客户端配置：command为SDK环境Python，args为`["-m","tcm.mcp_server"]`，cwd为仓库目录，env只配置`TCM_DB`、`ETCM_ENRICHMENT_DB`、`TCM_DATASET_VERSION`、`PHARMACOPOEIA_INDEX`等本地只读来源路径。不需要DeepSeek密钥。不把本地资料服务器配置到会自动把工具结果发送给外部模型的客户端，既有外发授权不因此扩大。

## 回滚与未完成范围

去掉`--mcp-python`，或将`RESEARCH_REPORT_TRANSPORT=direct`即可回到直接调用，无数据迁移。已经完成的报告历史仍可读取；执行中的checkpoint绑定执行配置，切换传输后旧checkpoint需重新发起任务，不能静默混用。

未完成：共同靶点MCP发布、模型自主选择MCP工具、远程鉴权与HTTP部署、连接池/高并发压测、第三方成品客户端验收，以及Windows取消清理测试。当前是两个业务工具及实际报告的本地互操作闭环，不是完整远程工具平台。Docker镜像和远程Git均未发布。

## 最终回执

6个受影响模块36项测试通过，0跳过、0网络尝试；真实双客户端协议验收及三题真实浏览器报告验收通过。未重跑全仓测试。见[验收总览](../audits/mcp_readonly_v1/summary.json)、[协议](../audits/mcp_readonly_v1/protocol.json)、[浏览器](../audits/mcp_readonly_v1/browser.json)、[相关回归](../audits/mcp_readonly_v1/regression.json)。
