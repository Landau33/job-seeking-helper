# job-seeking-helper

> Claude Code / Claude Agent Skill：`job-seek-planner`

中国大陆机器人 / 具身智能算法岗求职规划技能（VLA、RL、运动控制、WBC/WAM、manipulation、
sim2real、SLAM 方向）。

## 用法

在 Claude Code 里直接说「帮我找机器人算法岗的工作」，或者 `/job-seek-planner`。
技能会先做简短意图确认（方向 / 身份 / 城市 / 硬条件 / 交付偏好），再联网调研，
把结果写进 `jobs.json`，然后生成看板和表格。

## 目录

```
SKILL.md                    主流程（Claude 读这个）
reference/roles.md          方向词典、检索关键词、各方向面试考点
reference/companies.md      国内公司地图（检索起点，需现场核实）
reference/sources.md        调研渠道、来源分级、红线
assets/jobs_template.json   数据模板
assets/build_report.py      校验 + 单文件 HTML 看板 + Excel
assets/merge_shards.py      合并并行 agent 的分片研究文件
```

## 手动跑脚本

```bash
python3 assets/build_report.py <out>/jobs.json --html <out>/report.html --xlsx <out>/jobs.xlsx
python3 assets/build_report.py <out>/jobs.json --check-only          # 只校验
python3 assets/build_report.py <out>/jobs.json --merge-status s.json # 合并看板导出的投递状态
```

只有导出 Excel 需要 `XlsxWriter`；HTML 看板是零依赖的单文件。

另有 `assets/merge_shards.py`，把并行 agent 产出的分片合并成 `jobs.json`：

```bash
python3 assets/merge_shards.py <out>/agent_out <out>/jobs.json
```

## 打开看板

**推荐：用看板服务启动（改投递状态会自动写回 jobs.json）**

```bash
python3 <skill>/assets/serve.py <out>
```

然后浏览器打开 <http://127.0.0.1:8778/report.html>。右上角标签显示「自动同步已开启」即生效：
在卡片上改投递状态，会立刻写回 `jobs.json`（只改 投递状态 / 下一步 / 备注，写前备份为 `jobs.json.bak`）；
`jobs.json` 有更新时刷新页面会自动重建看板。服务只监听本机 127.0.0.1，只接受同源请求。
按 Ctrl+C 停止；换端口加 `--port 8780`；想在后台跑：`nohup python3 <skill>/assets/serve.py <out> >/tmp/jobboard.log 2>&1 &`，
停止用 `pkill -f "assets/serve.py"`。第一次用这种方式打开时，页面会把这个地址下浏览器里已有的、和 `jobs.json`
不一致的状态补推一次。

**方式一：双击文件**（最省事，但状态只存在浏览器里，不会写回 jobs.json）

```bash
open <out>/report.html
```

**方式二：普通静态服务器**（只读，同样不会写回 jobs.json；有了看板服务后一般不需要）

```bash
cd <out> && python3 -m http.server 8778
```

然后开 <http://127.0.0.1:8778/report.html>。后台跑加 `nohup ... &`；
查是否在跑 `lsof -nP -iTCP:8778 -sTCP:LISTEN`；停用 `pkill -f "http.server 8778"`。
提示 `Address already in use` 说明已经有一个在跑，换个端口或直接开浏览器。

### ⚠️ 两种方式的投递状态不互通

看板把投递状态存在浏览器的 localStorage 里，而 localStorage **按 origin 隔离**：
`file:///.../report.html` 和 `http://127.0.0.1:8778/report.html` 是两套独立存储。
**选一种方式固定用**，来回换会觉得"状态丢了"。同理，换浏览器、换端口号、
用无痕窗口，状态都不会跟过去。

存储 key 固定为 `jobseek_status_v1`（早期版本把数据的更新时间拼进 key，导致每次刷新
数据后状态读不到；现在打开看板会自动把历史 key 的内容合并回来）。

### 给状态做浏览器之外的备份

浏览器存储会被清缓存清掉，重要状态建议导出：

1. 看板上点「导出投递状态」，把那段 JSON 存成 `<out>/status.json`；
2. 刷新数据后合并回去：

```bash
python3 assets/build_report.py <out>/jobs.json --merge-status <out>/status.json
```

`merge_shards.py` 在合并 agent 分片时也会保护 `投递状态` / `下一步` / `备注`，
但那只保护 `jobs.json` 里已有的值——浏览器里还没导出的改动它看不到。

## 设计原则

- 薪资、HC、截止时间查不到就写 `未知`，不猜；社区来源一律标 `网传`。
- 每条影响投递决定的信息都要有 `来源`（链接 + 类型 + 访问日期），没查实的进 `存疑`。
- 只用公开职业信息，不收集私人联系方式；可以起草内推消息，但不代发。
- `jobs.json` 是唯一数据源，刷新时增量更新，不覆盖用户填的 `投递状态` 和 `备注`。
