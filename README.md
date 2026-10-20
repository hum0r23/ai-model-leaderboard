# 全球 AI 大模型综合能力排行榜(Live)

实时更新的全球大模型多维度排行榜。**打开页面即直抓最新数据**,无需手动维护:

- **智能程度** · **Terminal-Bench** · **数据成熟度** 为核心维度(源站若提供 Coding / Agentic 指数则自动补上)
- 综合分 = 智能 35% + 编码 25% + Agentic 20% + Terminal-Bench 20%(缺失维度自动归一)
- **维度自适应**:Artificial Analysis 于 2026-10 改版后,榜单表格载荷不再输出 `codingIndex` / `agenticIndex` / `evalTokenCounts`,页面会自动隐藏对应列与排序页签,综合分按实际可用维度重新归一,不做任何伪造填充
- 默认展示 **Top 20**,可切换维度排序、展开更多
- **纯实时模式**:榜单只展示实时源的原始数据,无任何手动填充条目

## 实时机制

| 层级 | 说明 |
|------|------|
| ① 实时主源 | 每次打开页面,浏览器直接抓取 [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models) 公开排行榜页并解析(该站当前未开放 CORS,多数环境会自动落到 ②) |
| ② 定时快照 | `.github/workflows/daily-fetch.yml` 每 6 小时自动抓取,写入 `data/llms.json`;实时源不可用时前端自动回退 |
| ③ 覆盖层(可选) | `overrides.json` 当前为空。如需临时补充 AA 尚未收录的新模型官方数据可追加到 `models[]`,AA 收录后按 slug/名称自动以实测为准 |

## 本地开发

```bash
# 首次生成快照
python3 scripts/fetch_aa.py

# 本地预览
python3 -m http.server 8000
open http://localhost:8000
```

## 目录

```
├── index.html             # 排行榜单页
├── styles.css             # 样式
├── app.js                 # 实时抓取 / RSC 解析 / 评分渲染
├── overrides.json         # 覆盖层(当前为空,纯实时模式)
├── scripts/fetch_aa.py    # 快照抓取器
├── data/llms.json         # 每日快照(由 GitHub Actions 更新)
└── .github/workflows/     # 每日自动抓取
```

## 数据归属

排行榜数据由 [Artificial Analysis](https://artificialanalysis.ai) 提供,使用需保留来源。本站不填充任何手动数据条目。

MIT License
