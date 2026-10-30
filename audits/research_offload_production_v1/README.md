# A正式循环：大结果外置交付（2026-10-29）

复用现有LangGraph核心；正式聊天/API现在按阈值保存完整观察，并通过同run引用回读。没有新增框架、外部模型调用或来源数据库Schema迁移。实施与复现见[说明](../../docs/RESEARCH_EVIDENCE_OFFLOAD.md)。

| 验收 | 原始证据 | 范围 |
|---|---|---|
| 完整离线回归 | [最终结果](ci_final.json) | 138模块1173项，0跳过/0联网尝试；新增19项已含在总数内 |
| 首轮兼容失败 | [初次结果](ci_initial.json)、日志（本地保留：`initial_failure_logs/test_research_loop_api.log`） | 旧无scope合成循环异路径恢复被证据路径绑定影响；修复后保留旧契约，不修改旧测试断言 |
| 原生动作/观察/回读 | `tests/test_research_offload.py` | 14000个合成标识，读回offset=7000处的标识；一资料查询、一证据回读，后者计入工具预算 |
| 真实本地worker | 同上 | 五个注册工具、14000标识疾病目录、画像及排序大结果外置；源数据库字节不变 |
| 三个真实SIGKILL | [最终结果](recovery_final.json)、[逐案状态](recovery/) | 证据已提交/引用未发布时停止；引用或下一动作已保存时续跑；每案底层查询1次 |
| Chromium交互 | [最终结果](browser_final.json)、[截图](browser_final.png) | 折叠、上一页/下一页、完整下载14000标识、同run刷新、无JavaScript错误；字体排版未验收 |
| 既有ETCM烟测 | [结果](real_data_smoke.json) | 当归画像＋川芎前5相似来源，2工具调用；21189字节返回实际外置、回读与下载SHA一致；无外发 |
| 存储与备份 | 同上测试及`test_research_evidence_store.py` | 所属run/owner、伪造引用、来源/哈希、不可变ID、配额、SQLite失败、活动锁、配套备份与缺证据拒绝 |

`targeted_initial.json`、`large_targeted_initial.json`、`browser_initial.json`保留最早接入问题：JSON持久化后的tuple/list比较、过宽异常捕获、Strict Schema拒绝URL数字字符串。后续修复与最终回归另存，不改判最初结果。

强杀记录中的`max_resumed_input_bytes`只计替身接收到的消息JSON，不含工具Schema，更不是提供方token。完整上下文投影（含Schema）的原始检查在逐案`result.json`的`run.context_checks`中。未验证真实模型质量、语义选择、计费节约、生产负载或整机断电。

保留未发布证据并计入配额；两个SQLite文件不具备联合事务。正式新聊天仍是请求绑定执行及终态回看，不宣称网页断点续跑已完成。备份保证已发布引用有内容，不能在代码/路径绑定改变时强行继续旧journal。

所有离线/浏览器/强杀资料均合成，另一次真实ETCM烟测只读取本地。7896预览已更新，状态目录不变，模型/联网/追踪关闭；临时7902合成服务已停止。未提交Git、构建或上传Docker。
