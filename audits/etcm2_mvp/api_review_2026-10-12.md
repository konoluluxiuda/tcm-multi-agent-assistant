# 药材 API 与查询页面修复验收

日期：2026-10-12。基于提交 `b77ebd1` 的修复验收；提交与上传状态以 Git 记录为准。没有重启现有医疗容器。

## 修复

1. 页面详情、筛选、比较按钮显式通过 `document.getElementById()` 读取输入，不依赖元素 ID 自动暴露为全局变量，避免与 `window.name` 冲突。
2. `/herbs` 检查所有查询参数：未知字段与合法字段混用也返回 400，空字符串、纯空白和重复参数均拒绝；不静默丢弃条件。
3. 筛选和比较保留扁平展示字段，同时附带 `fields`（含状态、原值及 JSON Pointer）、`source` 和 `warnings`。无法解析的比较项也保留中文映射等警告。
4. 页面结果使用 `textContent`，源字段中的 HTML 不会执行。
5. API 测试不再写入进程环境变量；临时设置、客户端与目录通过 cleanup 恢复。
6. 使用文档同步已有页面/API/筛选/比较能力，修正 `flavor` 为 `flavors`，列明 HTTP 依赖，独立服务示例使用本地端口 7863。

## 验证

| 检查 | 结果 |
|---|---|
| 数据层单测 | 14/14 通过 |
| API 单测 | 8/8 通过，含混合错误参数、空值、重复参数、来源保留、数据库缺失 |
| Chromium 页面检查 | 6/6 通过 |
| 隔离环境依赖检查 | `pip check` 通过 |

API 测试使用临时构造数据；浏览器通过临时本机 Uvicorn 服务读取已有 `local-2026-10-10` 数据库，未重新导入源数据。测试服务结束后自动关闭。

浏览器验证：设置已有 `window.name` 后查询 HuangQin 成功；family=Leguminosae 得到 127 条带字段来源记录；错误筛选字段显示错误；比较 HuangQin/HuaShi/missing 分别返回 ok/ambiguous_entity/not_found；模拟带 HTML 的响应以文本显示且未执行；页面无 JavaScript 错误。

环境：Python 3.13，FastAPI 0.115.11、Uvicorn 0.34.0、HTTPX 0.28.1、Starlette 0.46.1、AnyIO 4.8.0、Pydantic 2.10.6；Playwright 1.63.0 / Chromium 153。API 核心依赖对齐仓库 requirements.txt，隔离环境位于 `/tmp/tcm-api-check-20261012`，不是完整医疗应用环境。最初沙箱内 TestClient 调用未完成；对齐依赖后在允许本机进程通信的执行环境完成测试，未据此判断为应用故障。

## 复现

按 [使用说明](../../docs/ETCM2_MVP_USAGE.md) 安装轻量依赖后：

```bash
python -m unittest discover -s tests -p test_tcm_herbs.py -v
python -m unittest discover -s tests -p test_tcm_api.py -v
python -m pip install playwright==1.63.0
python -m playwright install chromium
python -m scripts.verify_tcm_page
```

[浏览器检查脚本](../../scripts/verify_tcm_page.py)要求已有本地示例版本数据库；[结果 JSON](page_checks_2026-10-12.json)。HTML 执行检查使用模拟响应，其余按钮查询使用真实本地数据库。

## 边界

中文名称映射仍暂缓；未接入原医疗聊天 Agent。`/health` 仅检查进程存活；筛选仍为原文子串匹配，未增加标准术语语义、分页或性能容量承诺。完整来源增加响应大小，后续应在保留可追溯性的前提下设计分页。比较仍逐项查询，导入时并发比较的批次一致性尚未专门处理。此轮不代表容器部署或生产验收。
