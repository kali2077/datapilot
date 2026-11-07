# DataPilot Token 决策(tokens)

> 3 层:primitive(`--dp-gray-*` 等)→ semantic(`--dp-text-1` 等)→ 组件映射(EP `--el-*`)。
> 定义处:`web/src/styles.css`;消费处:全部视图与图表。

## 颜色 · Light(`:root`)

| Token | 值 | 用途 |
|---|---|---|
| `--dp-accent` | `#2563eb` | 唯一 accent(主按钮/active/链接/图表主线) |
| `--dp-accent-hover` | `#1d4ed8` | accent 悬停 |
| `--dp-accent-active` | `#1e40af` | accent 按下 |
| `--dp-accent-soft` | `rgba(37,99,235,.08)` | accent 淡染底(active 导航/筛选/hero 卡) |
| `--dp-brand-2` | `#7c3aed` | 紫,仅 logo 渐变 |
| `--dp-bg` | `#f2f4f7` | 应用背景 |
| `--dp-bg-subtle` | `#f8fafc` | 次级底(表头/代码块/侧边栏) |
| `--dp-surface` | `#ffffff` | 卡片/面板 |
| `--dp-border` | `#e4e7ec` | 常规边框 |
| `--dp-border-soft` | `#f0f2f5` | 行分隔/弱边框 |
| `--dp-text-1` | `#101828` | 主文本 |
| `--dp-text-2` | `#344054` | 常规文本 |
| `--dp-text-3` | `#667085` | 次级/说明 |
| `--dp-ok` | `#16a34a` | 成功(低饱和) |
| `--dp-warn` | `#d97706` | 警告 |
| `--dp-danger` | `#dc2626` | 失败/危险 |
| `--dp-info` | `#2563eb` | 信息(复用 accent) |
| `--dp-side-bg` | `#f7f8fa` | 侧边栏淡染底(**非全黑**,Learned #1) |
| `--dp-side-border` | `#e8eaf0` | 侧边栏右缘 |
| `--dp-side-text` | `#414b5e` | 侧边栏文字 |
| `--dp-side-muted` | `#8b94a6` | 侧边栏图标/分组标题 |
| `--dp-terminal-bg` | `#0f172a` | 日志终端底(功能深色,主题无关) |

## 颜色 · Dark(`html.dark`)

| Token | 值 |
|---|---|
| `--dp-accent` / soft | `#3b82f6` / `rgba(59,130,246,.14)` |
| `--dp-bg` / subtle | `#0e1219` / `#151b26` |
| `--dp-surface` | `#161d29` |
| `--dp-border` / soft | `#262e3d` / `#1f2735` |
| `--dp-text-1/2/3` | `#e7ebf3` / `#c3cbd9` / `#939cb0` |
| `--dp-ok/warn/danger` | `#4ade80` / `#fbbf24` / `#f87171` |
| `--dp-side-bg` | `#12161f`(深一档 tint,非纯黑) |

## 形状/字体/阴影

- radius:`--dp-radius-sm 6px`(输入/小件)、`--dp-radius 8px`(卡片)、`--dp-radius-lg 12px`(弹窗)
- 间距:4/8/12/16/24 网格(dense)
- 字体:`--dp-font` = `'Inter Variable', -apple-system, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif`;`--dp-mono` = `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`
- 阴影:`--dp-shadow` = ambient+direct 分层(`0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.04)`);dark 下基本归零靠 tint
- 数字:`font-variant-numeric: tabular-nums` 全数据列/统计卡必加

## EP 变量映射(消灭 #409EFF)

`--el-color-primary` → `var(--dp-accent)`;`light-3/5/7/8/9` → soft→浅阶梯;`dark-2` → hover;
`--el-bg-color` → surface、`--el-bg-color-page` → bg、`--el-border-color(-light/-lighter)` → border/soft、
`--el-text-color-primary/regular/secondary` → text-1/2/3、`--el-fill-color-light` → bg-subtle、
`--el-color-success/warning/danger` → ok/warn/danger。
