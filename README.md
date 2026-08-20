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
```

## 手动跑脚本

```bash
python3 assets/build_report.py <out>/jobs.json --html <out>/report.html --xlsx <out>/jobs.xlsx
python3 assets/build_report.py <out>/jobs.json --check-only          # 只校验
python3 assets/build_report.py <out>/jobs.json --merge-status s.json # 合并看板导出的投递状态
```

只有导出 Excel 需要 `XlsxWriter`；HTML 看板是零依赖的单文件，双击就能开。

## 设计原则

- 薪资、HC、截止时间查不到就写 `未知`，不猜；社区来源一律标 `网传`。
- 每条影响投递决定的信息都要有 `来源`（链接 + 类型 + 访问日期），没查实的进 `存疑`。
- 只用公开职业信息，不收集私人联系方式；可以起草内推消息，但不代发。
- `jobs.json` 是唯一数据源，刷新时增量更新，不覆盖用户填的 `投递状态` 和 `备注`。
