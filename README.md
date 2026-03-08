
# 言之有品 · AI 驱动的电商评论分析平台

## 📌 项目简介

**言之有品** 是一个基于 AI 的智能评论分析工具，能够自动分析电商评论数据，识别关键词、分类评价、生成改进建议。

### 核心功能
- 🤖 **AI 智能分类**：自动识别有效好评、有效差评、刷评、恶意评价
- 🏷️ **关键词提取**：自动提取买家最在乎的词语
- 📊 **维度分析**：识别评论涉及的评价维度（质量、价格、物流等）
- 📈 **可视化展示**：生成交互式网页仪表板
- 💡 **智能建议**：基于分析结果生成改进建议

---

## 🚀 快速开始

### 环境要求
- Python 3.8+
- 智谱 API Key（免费申请：https://open.bigmodel.cn/）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 API Key

#### Windows 系统：
1. 右键点击"此电脑" → 属性 → 高级系统设置 → 环境变量
2. 点击"新建"（用户变量）
3. 变量名：`ZHIPUAI_API_KEY`
4. 变量值：你的 API Key
5. 点击确定，重启 VSCode 或命令行

#### Mac/Linux 系统：
```bash
export ZHIPUAI_API_KEY="your_api_key_here"
```

#### 验证配置：
```bash
# Windows
echo %ZHIPUAI_API_KEY%

# Mac/Linux
echo $ZHIPUAI_API_KEY
```

### 运行分析

准备你的 CSV 文件，确保包含以下列：
- `review_content` - 评论内容
- `rating` - 评分（1-5）
- `review_time` - 评论时间
- `product_model` - 产品型号
- `likes` - 点赞数

然后运行：
```bash
python analysis.py
```

### 查看结果

分析完成后，用浏览器打开 `dashboard.html`：
```bash
# 如果你有 Live Server 扩展（推荐）
右键 dashboard.html → Open with Live Server

# 或者直接打开文件
双击 dashboard.html
```

---

## 📁 项目结构

```
言之有品/
├── analysis.py                    # 核心分析脚本
├── dashboard.html                 # 网页展示面板
├── requirements.txt               # 依赖包列表
├── bg.jpg                         # 背景图片
├── README.md                      # 项目说明（本文件）
├── .env                           # API Key 配置（本地使用，不上传）
├── CSV_数据文件.csv               # 你的评论数据
├── 结构化分析结果.json            # 分析结果（自动生成）
└── 最终分析报告/                  # 词云图等输出文件夹（自动生成）
    ├── 产品名称1/
    │   ├── 好评词云.png
    │   └── 差评词云.png
    └── 产品名称2/
        ├── 好评词云.png
        └── 差评词云.png
```

---

## 🔄 工作流程

```
CSV 数据输入
    ↓
[1] 品类识别 + 维度生成（AI）
    ↓
[2] 逐条评论分类（AI） → 区分有效评价、刷评、恶意评价
    ↓
[3] 关键词提取（AI） → 好评词、差评词
    ↓
[4] 词云生成 → 生成好评/差评词云图
    ↓
[5] 建议生成（AI） → 给商家的改进建议
    ↓
JSON 结构化结果 + 网页仪表板
    ↓
dashboard.html 可视化展示
```

---

## 📊 输出说明

### `结构化分析结果.json`
包含每个产品的完整分析：
- 总评论数、有效评价统计
- 评论分类分布（有效好评、有效差评、刷评等）
- 关键词及权重
- 维度提及统计
- AI 生成的改进建议

### `dashboard.html`
交互式网页，展示：
- 📊 评论分布图表
- 👍 好评关键词气泡
- 👎 差评关键词气泡
- 📐 维度提及排行
- 💬 AI 建议文本

---

## ⚙️ 高级配置

### 修改分析参数

打开 `analysis.py`，找到这些配置项：

```python
# LLM 模型选择
MODEL = "glm-4-flash"  # 改成 "glm-4" 获得更高精度（但更慢、更贵）

# 温度参数（0-1，越低越确定，越高越创意）
temperature=0.3  # 分类任务用低温度
temperature=0.6  # 建议生成用中等温度
```

### 修改网页配色

打开 `dashboard.html`，修改 CSS 变量：

```css
:root {
    --primary: #667eea;      /* 主色调 */
    --good: #10b981;         /* 好评颜色 */
    --bad: #ef4444;          /* 差评颜色 */
    --background: #0f172a;   /* 背景色 */
}
```

---

## 🆘 常见问题

### Q: 运行时出现"No API Key"？
**A:** 检查你的环境变量是否正确配置。重启 VSCode 或命令行后重试。

### Q: 词云图片加载不出来？
**A:** 确保 `最终分析报告/` 文件夹在 `dashboard.html` 的同级目录。

### Q: GitHub 上 API Key 会泄露吗？
**A:** 不会。本项目使用环境变量存储，GitHub 上不会上传 `.env` 文件。

### Q: 能用自己的 LLM 替换智谱吗？
**A:** 可以！修改 `call_glm()` 函数，改成调用 OpenAI、Claude 或其他 API。

---

## 📈 下一步

- [ ] 支持多语言分析（英文、日文等）
- [ ] 添加情感分析细度控制
- [ ] 支持实时数据接入（API 或爬虫）
- [ ] 生成 PDF 报告
- [ ] 部署到云端（Flask/FastAPI）

---

## 📜 许可证

MIT License - 自由使用和修改

---

## 💬 反馈 & 支持

如有问题或建议，欢迎提 Issue 或 PR！

**作者**：言之有品团队  
**最后更新**：2026年3月  
**项目链接**：https://github.com/your-username/yanzhi-youpin
