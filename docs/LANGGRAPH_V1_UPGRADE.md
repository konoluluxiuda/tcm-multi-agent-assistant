# LangGraph 1.x整体升级记录

2026-10-28。主研究运行时由0.3.9升级至1.2.18；项目`.venv`已安装并启用新版。新版本地预览：http://127.0.0.1:7895/ 。源码、主依赖、CI和Docker研究构建配置已更新；当前WSL无法调用Docker，尚未生成或发布新镜像。

## 固定版本

| 组件 | 升级前 | 本轮固定 |
| --- | --- | --- |
| LangGraph | 0.3.9 | 1.2.18 |
| langchain-core | 0.3.44 | 1.7.0 |
| langchain-openai | 0.3.8 | 1.7.3 |
| langgraph-checkpoint | 2.0.19 | 4.3.1 |
| PostgreSQL checkpoint适配器 | 2.0.16 | 3.2.0 |
| OpenAI协议SDK | 1.66.3 | 2.54.0 |
| 可选LangChain/Qdrant适配器 | 0.2.0 | 1.1.0 |
| 可选Qdrant客户端 | 1.13.3 | 1.20.0 |

LangGraph版本依据当日[官方PyPI发布](https://pypi.org/project/langgraph/1.2.18/)冻结。兼容迁移参考[LangGraph v1](https://docs.langchain.com/oss/python/migrate/langgraph-v1)与[LangChain v1](https://docs.langchain.com/oss/python/migrate/langchain-v1)官方指南。

`requirements-research-chat.txt`与`constraints-research-chat.txt`锁定主研究运行时；CI直接复用它们。PostgreSQL包由`requirements-checkpoint-postgres.txt`安装。新增`requirements-research-rag.txt`提供原RAG模块的现代适配器与classic存储兼容包；完整`requirements.txt`中的相关框架及必要依赖版本同步更新。

本机使用Python3.12.3；Docker仍为Python3.11。3.11的所有研究/检查点依赖二进制wheel已成功解析，不等于已经在3.11容器中实跑测试。没有安装或启动原有CV、语音等全部重型引擎；这部分仅同步框架依赖，未做模型/端到端验收。

## 实现变化

- 单药/两药checkpoint图使用原生`durability="sync"`，确保下一步开始前checkpoint写入完成。移除两药图为0.3.x增加的内部逐页interrupt/重启循环；用户无须逐页确认。
- 增加`framework_versions()`：PostgreSQL执行绑定、两药执行契约、持久化模型循环journal同时绑定LangGraph及SDK版本。版本变化拒绝续跑旧契约，避免仅源文件不变就跨运行时恢复。
- 两药契约版本升为3；业务流程仍采用现有StateGraph，无需重写为其他框架或强制采用预构建Agent。
- 原RAG的存储导入迁移至`langchain_core.stores`及`langchain_classic.storage`；Qdrant generation记录实际SDK版本，移除硬编码旧版本。
- 全量回归暴露此前覆盖`research_run_durable.py`时丢失的`local_profile`兼容审计入口，已恢复原取消/失败记录行为。保留首次失败日志，不以修改测试绕过该问题。
- Docker研究构建增加LangGraph版本断言，防止旧依赖层被当成新版构建；GitHub Actions缓存键覆盖共享约束和依赖入口。

## 验收

| 检查 | 结果与边界 |
| --- | --- |
| 最终完整离线CI | **136模块、1150项测试通过，0跳过、0网络尝试**；403个Python文件语法检查 |
| 项目`.venv`依赖检查 | `pip check`通过，独立升级环境同样通过 |
| 模型协议 | 真实SDK＋MockTransport的tool_calls、输出预算、错误不重试测试通过；本轮没有外部模型调用 |
| PostgreSQL与浏览器强杀 | 独立库、真实31秒租约到期、同Cookie点击恢复通过；四个来源读各一次、第一页一次、第二页两次（含被中断的一次） |
| 业务与页面 | 显示/下载23项及双方出处；第二页超时保留20项和后续按钮；无JS错误 |
| Qdrant兼容 | 本地合成向量写入、query_points及存储字节往返通过；无模型下载、无真实索引重建 |
| 新版预览 | 首页、health、药典就绪及数据库介绍接口均200，外部模型关闭 |
| Docker | WSL提示Docker命令不可用，未构建镜像；未执行push |

浏览器沿用合成数据库夹具；不是实际药材结论正确率。截图环境中文字体和站外样式受限，DOM及功能断言通过，不作为视觉排版验收。

[验收摘要](../audits/langgraph_v1_upgrade_v1/summary.json) · [全量CI](../audits/langgraph_v1_upgrade_v1/ci_final.json) · [浏览器](../audits/langgraph_v1_upgrade_v1/browser.json) · [依赖环境](../audits/langgraph_v1_upgrade_v1/environment.json) · [Qdrant烟测](../audits/langgraph_v1_upgrade_v1/qdrant_smoke.json)

首次全量CI因兼容入口缺失失败，原记录保留于`var/langgraph_v1_upgrade_v1/ci`；修复后的完整重跑记录在`ci_final`，不是只替换失败模块的汇总。

## 启动新版

```bash
source .venv/bin/activate
python -m pip check
python -m scripts.preview_local_tasks \
  --port 7895 --state var/langgraph_v1_upgrade_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --synthetic-loop
```

本轮该服务已启动；不要在同端口重复启动。PID记录在`var/langgraph_v1_upgrade_v1/preview.pid`，日志在同目录`preview.log`。默认模型调用关闭，资料仍使用既有本地索引。

其他机器创建Python3.11及以上虚拟环境后安装：

```bash
python -m pip install -r requirements-research-chat.txt
# 按需启用
python -m pip install -r requirements-checkpoint-postgres.txt
python -m pip install -r requirements-research-rag.txt
```

Python版本、依赖和源码应作为同一部署单元固定。项目新环境位于`.venv`，不依赖本轮临时安装目录存活。原`/tmp/tcm-ci-mail-env`保持旧版，不能将其与升级后的源码混用为新部署。

## 数据与回滚

原始资料、旧会话及checkpoint库未自动迁移。新版预览使用独立状态目录，旧浏览器会话记录不会自动出现在该目录。新版PostgreSQL验收库为`tcm_langgraph_v1_20261028`；旧`tcm_runtime`、`tcm_browser`及之前合成库均保留。

不要在旧库直接运行新版saver setup来尝试续跑。需要保留旧运行时查看历史或恢复时，使用其对应源码、依赖与库；新任务进入新执行契约。升级不会使模型循环的pending未知调用自动重发；已有拒绝重复派发策略保持。

回滚材料在[audits/langgraph_v1_upgrade_v1/rollback](../audits/langgraph_v1_upgrade_v1/rollback)：升级前依赖文本、旧版兼容实现及定向逆向patch。patch只撤销本轮框架代码变化，保留此前全分页工作；已通过`git apply --check`，没有实际覆盖当前代码。执行回滚前先停止对应worker并备份当前状态；不要让新旧worker共用队列。

```bash
# 审核并确认工作区没有后续改动后，才实际应用回滚材料
git apply --check audits/langgraph_v1_upgrade_v1/rollback/restore_code.patch
```

回滚也需要恢复相应旧依赖文件及旧环境，并撤去仅适用于新版的测试/可选安装入口。此patch不是自动回滚数据库的脚本；没有验证任意新版checkpoint可降级读取，不允许逆向迁移覆盖原库。

待Docker Desktop开启WSL集成后，可从当前依赖构建本地研究镜像再做容器验收。当前已完成源码和本机运行时升级；镜像构建与运行是明确未完成项，不声称已发布。
