# DataPilot 设计简报(brief)

> ui-craft 设计记忆。本文件是该项目的持久化设计上下文,任何 UI 任务先读这里。

## 1. 产品身份

- **产品**:DataPilot —— AI-Native 大数据平台(数据源/元数据/资产/集成/查询/调度/监控/AI 助手)
- **受众**:数据工程师、分析师、平台管理员(操作者,8 小时盯屏)
- **语言**:全中文界面;代码/SQL/日志/ID 为英文等宽
- **品牌色**:蓝 `#2563eb`(唯一 accent)+ 紫 `#7c3aed`(仅 logo 标识渐变,不用于其他表面)

## 2. 风格锁定

- **变体**:`ui-craft-dense-dashboard`(CRAFT=7 / MOTION=3 / DENSITY=9)
- **主题方向(用户已选定,2026-11-07)**:**方案 A「淡染清爽」** —— 浅色淡染侧边栏(非全黑)、单蓝 accent、hero 指标卡层级、状态圆点化、面积图趋势
- **暗色模式**:提供(Header 切换,`html.dark` + localStorage `dp_theme`)
- **accent 预算**:首屏 3–5 处(主按钮、active 导航、hero 数字、active 筛选)

## 3. Learned constraints(用户纠正记录,约束级)

1. **侧边栏禁止全黑深底**(2026-11-07,用户选定 A):全黑侧边栏是 AI 模板标志;用浅色淡染 `--dp-side-bg`,暗色主题下用深一档 tint 而非纯黑。
2. **先出风格预览再动工**(2026-11-07):大改 UI 前先做可切换的 mockup 给用户选定方向。

## 4. 反模式红线(本项目适用)

- 不做紫色/青色渐变装饰(紫仅 logo);不做 emoji 图标;不做彩虹图表
- 时间序列用面积/线图,分类对比用横向条形;禁饼图/3D
- 统计卡禁止等权重网格:1 hero + 次级卡
- 变化文本用中性三级色纯文本,禁红绿 pill/箭头
- 工具栏不放 solid primary;筛选用 ghost + accent 淡染 active
- `transition` 禁 `all`;表格式动效区域禁入场 stagger

## 5. 技术栈约束

- Vue 3 + Element Plus 2.9(全量引入,无 SCSS 定制 → 主题只能走 CSS 变量覆盖)
- ECharts 5.6(主题经 `charts/theme.js` 注册);LineageGraph 为手写 SVG(用 CSS 变量)
- 字体:Inter Variable(拉丁/数字)+ PingFang SC(中文)+ JetBrains Mono(代码),@fontsource 本地包
