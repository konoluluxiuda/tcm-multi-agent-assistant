# 全分页报告：浏览器、队列与真实强杀验收

> 历史阶段记录（2026-10-29标记）：2026-10-28分页验收快照。文中LangGraph 0.3及其checkpoint写入分析是原实验环境；当前依赖已升级1.x，恢复兼容及增量检查见[升级记录](LANGGRAPH_V1_UPGRADE.md)。不要把旧运行时说明当作新版本通用行为。 当前进度与后续任务统一见[大纲](TCM_PROJECT_ROADMAP.md)。

2026-10-28。已完成真实Chromium、FastAPI页面、PostgreSQL任务队列及checkpoint恢复验收。全部业务查询使用合成目录，外部模型调用0次；未更新Docker或常用预览服务。

## 发现并修复的问题

第一次完整强杀实验发现：第二页已经开始执行，但第一页checkpoint尚未落盘；杀掉HTTP进程后，第一页被重复查询。此前MemorySaver测试通过，不能覆盖PostgreSQL写入时序。

当前LangGraph 0.3运行时异步写checkpoint。正式两药图现在在每个共同靶点页节点之后设置内部interrupt边界，等待本次astream完整退出、pending写入完成，再从checkpoint自动继续下一节点。这个内部边界不要求用户逐页确认。

新增回归用例故意延迟第一页checkpoint写入，确认写入完成前不会派发第二页。与原有针对性回归合计54项通过。未升级框架、未引入第二套生产循环。

## 浏览器实测

1. 在页面提交Alpha/Beta“全部共同靶点”报告，实际进入后台队列。
2. 第二页工具开始后SIGKILL本次测试HTTP服务；等待真实31秒租约到期，未修改数据库时间戳。
3. 使用同一浏览器Cookie，重启后从任务历史点击“继续执行”。
4. 两侧文本和结构化查询各一次；第一页一次；第二页两次，其中第一次在查询结果产生前被中断。完成结果显示23项，下载JSON同样包含23项及双方出处。
5. 新任务注入第二页超时，页面显示未完成说明并保留第一页20项，后续查询按钮可见。

没有JavaScript运行错误，使用Playwright自带Node完成JS语法检查。本轮只验证后续查询按钮显示，未点击它去查询真实聊天目录中的合成来源ID。

浏览器阻止所有站外资源。截图环境缺少中文字体，出现方框字，且CDN样式不可用；因此截图只留作结构/状态记录，不作为真实用户浏览器的字体与视觉验收。DOM中文文本断言正常通过。

## 环境及边界

- 使用现有本机Unix socket PostgreSQL，独立库`tcm_pair_pages_20261028`。原任务库未迁移或清空。
- 第一次`pg_ctl status`在受限进程环境中误报未运行；实际socket探测确认已有服务运行，启动尝试被其锁文件阻止，没有移除锁文件或重复启动实例。
- 测试服务使用独立7893端口及新状态目录，最终已关闭。PostgreSQL原服务保持运行，合成测试库保留便于审计。
- 当前研究面板由疾病目录配置控制。初次测试启动遗漏`--diseases`导致面板不出现，已修正测试启动参数；此历史耦合未在本轮重构。
- 测试入口通过真实页面与队列，但给报告执行注入了合成数据库适配器，未进行真实药典/ETCM的业务正确性验收。
- 只保证已保存页面不重查；未保存的只读页允许恢复重查，不声称exactly-once工具执行。旧代码与新代码的执行fingerprint不同，不强行续跑旧契约任务。

## 证据与复现

[验收摘要及失败历史](../audits/pair_pagination_browser_v1/summary.json) · [源码及运行产物哈希](../audits/pair_pagination_browser_v1/manifest.json)

[浏览器脚本](../scripts/check_pair_pagination_browser.py) · [合成HTTP夹具](../tests/pair_pagination_http_fixture.py)

运行产物在`var/pair_pagination_browser_v3`：完整与部分结果截图、合成JSON下载、工具调用记录。第一次真实强杀失败记录保留在`var/pair_pagination_browser_v2`，未用成功记录覆盖。

需要已初始化的独立测试PostgreSQL库，两个DSN设置为该库；不要用有真实任务的队列运行强杀夹具。浏览器脚本只终止自己启动的HTTP进程。

```bash
/tmp/tcm-api-check-20261012/bin/python -m scripts.check_pair_pagination_browser \
  --out var/pair_pagination_browser_new \
  --server-python /tmp/tcm-ci-mail-env/bin/python \
  --browser /tmp/tcm-playwright-20261012/chromium-1243/chrome-linux64/chrome
```

后续可在选定的本地常用部署应用已验收代码；目前不能宣称用户打开的原服务已更新。本轮不修改路线图，不进行Git提交或远程发布。
