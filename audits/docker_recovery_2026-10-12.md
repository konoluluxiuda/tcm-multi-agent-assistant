# Docker 恢复与药材服务部署记录

日期：2026-10-12，Asia/Shanghai。上轮 Git 已同步至 `fea2f83`；本报告记录随后进行的本地 Docker 恢复与新增部署。

## 结果

| 服务 | 容器 | 本机地址 | 当前状态 |
|---|---|---|---|
| 原医疗主应用 | medical-assistant-current | http://127.0.0.1:7860 | running / healthy；首页和 /health 返回 200 |
| 独立药材查询 | tcm-herb-query-tcm-query-1 | http://127.0.0.1:7863 | running / healthy；/ready 返回 2,076 条有效来源记录 |

两个容器均为 `unless-stopped`。原测试容器没有启动。本次不重建原医疗镜像，恢复原容器；主容器继续使用已有工作区挂载。主应用恢复说明进程和首页可用，本轮未重新调用聊天模型、Tavily 或验证所有医疗功能。

## 原停止情况

检查时主容器及两项测试容器为 Exited (255)，停止时间几乎相同，OOMKilled=false；主容器此前策略为 no。日志尾部是正常健康检查，没有可确定退出原因的异常。Docker Desktop / WSL 停机是候选解释，未据此认定根因。

执行 `docker update --restart unless-stopped medical-assistant-current` 后启动主容器；首页和健康检查均通过。手动停止的容器仍保持停止，策略不保证自动启动 Docker Desktop 本身。

## 新增药材镜像

- 配置：[Dockerfile.tcm](../Dockerfile.tcm)、[compose.tcm.yaml](../compose.tcm.yaml)、[轻量依赖](../requirements-tcm.txt)。
- 本地标签：`tcm-herb-query:dev`。
- 镜像 ID：`sha256:4ca4280231113efaef49c2a22f698608464ec50a090281db03748aa74bdf2dac`。
- 镜像大小：210,799,285 字节，约 211 MB / 201 MiB。
- Python 3.11，复制 tcm 模块与轻量依赖；无原医疗图像模型、原始数据集或密钥。
- 数据库挂载：`var/tcm/herbs.sqlite3` → `/data/herbs.sqlite3`，只读。
- 当前版本：`local-2026-10-10`。
- 新增 `/ready`：检查 Schema、版本与有效记录数。数据库不可用、版本不存在或无有效记录返回 503。`/health` 继续表示进程存活。

镜像健康检查使用 `/ready`。运行目录只读，非 root 用户；只对本机映射 7863。代码位于镜像内，后续修改需重新构建。

## 验证

| 检查 | 结果 |
|---|---|
| 主机隔离环境的数据层/API 回归 | 26/26 通过 |
| 新镜像内 Python 3.11 回归 | 26/26 通过 |
| 真实服务 HTTP 检查 | 10/10 通过 |
| Docker 容器健康状态 | 两项运行服务均 healthy |

HTTP 覆盖：原首页与健康检查；药材首页、存活、数据库就绪、HuangQin 详情、来源 ID、分页、歧义比较、错误筛选。详细结果见 [检查 JSON](docker_recovery_2026-10-12.json)。镜像测试只挂载 tests 目录，使用临时测试数据库，不修改真实数据。

## 操作与边界

```bash
docker compose -f compose.tcm.yaml up -d --build
docker compose -f compose.tcm.yaml ps
docker compose -f compose.tcm.yaml logs --tail 50
```

见 [使用说明](../docs/ETCM2_MVP_USAGE.md)。无需为独立药材服务传入聊天、嵌入或 Tavily 密钥。

Docker 账户 `8n1eld` 用于镜像仓库；GitHub 仓库属于 `konoluluxiuda`，二者不必相同。本轮没有推送 Docker Hub，也没有把新镜像标成已发布。中文名称映射、原医疗聊天与药材 repository 集成仍未完成。
