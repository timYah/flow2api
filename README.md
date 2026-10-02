# Flow2API

<div align="center">

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.119.0-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)

**一个功能完整的 OpenAI 兼容 API 服务，为 Flow 提供统一的接口**

</div>

## ❤️赞助商

<div align="center">

[![FastAIToken](static/sponsors/fastaitoken-banner.png)](https://www.fastaitoken.com/register)

</div>

**FastAIToken** 是面向开发者的 AI API 聚合平台，支持 OpenAI、Claude、Gemini 等主流大模型，兼容 OpenAI API 协议，可无缝接入 **Claude Code、Codex、Gemini CLI、Cherry Studio、Cline、Continue** 等各类 AI 开发工具。平台采用 **充值 1:1（1 元 = 1 美元 API 额度）**，帮助开发者以更低成本、更高效率地使用全球领先的大模型服务。

平台提供多个可选分组与公开状态页，开发者可根据成本、响应速度和稳定性自由选择不同渠道，并享受 **7×24 小时真人技术支持**（非机器人）。

**主要做 AI 开发接入？可以试试 [FastAIToken](https://www.fastaitoken.com/register)，兼容 Codex / Claude Code / Gemini CLI 等主流工具。**

---

<table>
<tr>
<td width="180" align="center" valign="middle">
  <a href="https://www.fastaitoken.com/register">
    <img src="static/sponsors/fastaitoken-logo.png" alt="FastAIToken" width="150">
  </a>
</td>
<td valign="top">
  感谢 <strong>FastAIToken</strong> 赞助了本项目！FastAIToken 是面向开发者的 AI API 聚合平台，兼容 OpenAI API 协议，支持 Claude Code、Codex、Gemini CLI、Cherry Studio、Cline、Continue 等主流 AI 开发工具。<br><br>
  当前提供 <strong>0.02x OpenAI 福利分组（限时）</strong>、<strong>0.25x OpenAI 普通分组</strong>、<strong>0.35x OpenAI 备用分组</strong>、<strong>0.45x OpenAI Pro 分组</strong>、<strong>0.7x Claude 普通分组</strong>、<strong>1.2x Claude Max 渠道</strong>；支持 <strong>1 元 = 1 美元 API 额度</strong> 的充值比例，并提供公开状态页、企业开票、<strong>99% SLA 企业级稳定号池</strong> 与 <strong>7×24 小时真人技术支持</strong>。<br><br>
  欢迎通过<a href="https://www.fastaitoken.com/register">此链接</a>注册体验。
</td>
</tr>
</table>

## ✨ 核心特性

- 🎨 **文生图** / **图生图**
- 🎬 **文生视频** / **图生视频**
- 🎞️ **首尾帧视频**
- 🔄 **AT/ST自动刷新** - AT 过期自动刷新，ST 过期时自动通过浏览器更新（personal / extension 模式）
- 📊 **余额显示** - 实时查询和显示 VideoFX Credits
- 🚀 **负载均衡** - 多 Token 轮询和并发控制
- 🌐 **代理支持** - 支持 HTTP/SOCKS5 代理
- 📱 **Web 管理界面** - 直观的 Token 和配置管理
- 🎨 **图片生成连续对话**
- 🧩 **Gemini 官方请求体兼容** - 支持 `generateContent` / `streamGenerateContent`、`systemInstruction`、`contents.parts.text/inlineData/fileData`
- ✅ **Gemini 官方格式已实测出图** - 已使用真实 Token 验证 `/models/{model}:generateContent` 可正常返回官方 `candidates[].content.parts[].inlineData`

## 🚀 快速开始

### 前置要求

- Docker 和 Docker Compose（推荐）
- 或 Python 3.9+ 与 [uv](https://docs.astral.sh/uv/)
- `personal`（nodriver）打码模式需要 Python 3.10+；Python 3.9 仍可运行其他模式

- 由于Flow增加了额外的验证码，你可以自行选择使用浏览器打码或第三发打码：
注册[YesCaptcha](https://yescaptcha.com/i/13Xd8K)并获取api key，将其填入系统配置页面```YesCaptcha API密钥```区域
- YesCaptcha 支持在管理页切换 `type`：`RecaptchaV3TaskProxyless`、`RecaptchaV3TaskProxylessM1`、`RecaptchaV3TaskProxylessM1S7`、`RecaptchaV3TaskProxylessM1S9`；当前默认推荐 `M1S9`，S7/S9 会强制提交 `minScore` 0.7/0.9。
- 默认 `docker-compose.yml` 建议搭配第三方打码（yescaptcha/capmonster/ezcaptcha/capsolver）。
如需 Docker 内有头打码（browser/personal），请使用下方 `docker-compose.headed.yml`。

- 自动更新st浏览器拓展：[Flow2API-Token-Updater](https://github.com/TheSmallHanCat/Flow2API-Token-Updater)

#### Chrome 验证码扩展（extension）

仓库内的 [`extension/`](extension/) 是验证码扩展。图片生成使用扩展模式时，扩展会在已登录的 Chrome Flow 项目页内完成 reCAPTCHA 和 Flow 请求提交，确保 token 与 Chrome 的 Cookie、网络和浏览器环境一致。它与上面的 Token 同步插件不是同一个扩展。

1. 启动 Flow2API，并在管理台的“系统配置”中确认 API Key。
2. 在 Chrome 地址栏打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择本仓库的 `extension/` 目录。
3. 打开扩展详情中的“扩展程序选项”，填写：
   - `WebSocket URL`：本机默认填写 `ws://127.0.0.1:8000/captcha_ws`
   - `Flow2API API Key`：管理台“API 密钥配置”中的 API Key
   - `Route Key`：单个扩展实例的标识，可选；多账号使用时每个 Chrome 实例填写不同值
   - `Client Label`：可选的人类可读名称
4. 回到管理台“系统配置 → 验证码配置”，将“打码方式”设为 `Chrome 扩展打码`。
5. 在每个 Token 的详情中填写“扩展路由键”，并确保它与目标 Chrome 扩展的 `Route Key` 完全一致。未填写 Route Key 的扩展连接只用于未指定路由的 Token。

扩展需要保持 Chrome 运行并保持 Google Labs 登录状态。扩展设置页的“运行状态”会显示 WebSocket 连接、当前任务、最近错误和最近 100 条脱敏日志；若生成请求提示扩展超时或脚本失败，先在该页面查看具体阶段，再检查 WebSocket URL、API Key、Route Key 是否一致。

更新仓库中的扩展文件后，请在 `chrome://extensions` 对该扩展点击“重新加载”，确认版本显示为 `1.2.5`，否则 Chrome 仍会运行旧版后台脚本。

如果外部生成工具启用了“自动 Flow2API project ID”，它仍会将自动生成的 project ID 传给 API，并将请求绑定到该项目所属账号。扩展脚本或连接故障不会禁用该账号；如果账号此前已因旧版本故障被自动禁用，需要在管理台手动启用一次。

### 方式一：Docker 部署（推荐）

#### 标准模式（不使用代理）

```bash
# 克隆项目
git clone https://github.com/TheSmallHanCat/flow2api.git
cd flow2api

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

> 说明：Compose 已默认挂载 `./tmp:/app/tmp`。如果把缓存超时设为 `0`，语义是“不自动过期删除”；若希望容器重建后仍保留缓存文件，也需要保留这个 `tmp` 挂载。

#### WARP 模式（使用代理）

```bash
# 使用 WARP 代理启动
docker-compose -f docker-compose.warp.yml up -d

# 查看日志
docker-compose -f docker-compose.warp.yml logs -f
```

#### Docker 有头打码模式（browser / personal）

> 适用于你有虚拟化桌面需求、希望在容器里启用有头浏览器打码的场景。  
> 该模式默认启动 `Xvfb + Fluxbox` 实现容器内部可视化，并设置 `ALLOW_DOCKER_HEADED_CAPTCHA=true`。  
> 仅开放应用端口，不提供任何远程桌面连接端口。
> `personal` 内置浏览器现在默认按有头模式启动；如需临时切回无头，可额外设置环境变量 `PERSONAL_BROWSER_HEADLESS=true`。

```bash
# 启动有头模式（首次建议带 --build）
docker compose -f docker-compose.headed.yml up -d --build

# 查看日志
docker compose -f docker-compose.headed.yml logs -f
```

- API 端口：`8000`
- 进入管理后台后，将验证码方式设为 `browser` 或 `personal`

`browser` 模式会为每个 Token 使用独立的持久化 Chrome profile，目录位于
`browser_data_rt/accounts/token_<token_id>`。浏览器按请求懒启动，`browser_count`
表示同时运行的账户浏览器最大数量；达到上限时优先回收空闲浏览器，profile 不会被删除。
使用 Docker 有头模式时请保留 `./browser_data_rt:/app/browser_data_rt` 挂载，以便重建容器后继续复用登录状态。

### 方式二：本地部署

```bash
# macOS/Linux 安装 uv（已有 uv 可跳过）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆项目
git clone https://github.com/TheSmallHanCat/flow2api.git
cd flow2api

# uv 会根据 .python-version 创建项目虚拟环境并按 uv.lock 安装依赖
uv sync

# 启动服务
uv run python main.py
```

开发/运行测试：

```bash
uv run python -m pytest
```

### 首次访问

服务启动后,访问管理后台: **http://localhost:8000**,首次登录后请立即修改密码!

- **用户名**: `admin`
- **密码**: `admin`

## 📈 监控接口

- `GET /health`：公开健康检查，返回服务是否存活、活跃 Token 数、即将过期 Token 数、已过期 Token 数、429 禁用数等摘要
- `GET /metrics`：Prometheus 指标接口
- `GET /api/tokens`：管理接口，返回 `at_expires`、`at_expired`、`at_expiring_within_1h`、`ban_reason`、`consecutive_error_count` 等 Token 状态

Prometheus 可直接抓 `/metrics`。如果部署到 Kubernetes，建议只在集群内抓取，并在 Ingress/Gateway 层单独限制 `/metrics` 的外部访问。

### 模型测试页面

访问 **http://localhost:8000/test** 可打开内置的模型测试页面，支持：

- 按分类浏览所有可用模型（图片生成、文/图生视频、多图视频、视频放大等）
- 输入提示词一键测试，流式显示生成进度
- 图生图 / 图生视频场景支持上传图片
- 图片生成支持控制生成数量（1~4 张）
- 生成完成后直接预览图片或视频

## 📋 支持的模型

### 图片生成

| 模型名称 | 说明| 尺寸 |
|---------|--------|--------|
| `gemini-3.0-pro-image-landscape` | 图/文生图 | 横屏 |
| `gemini-3.0-pro-image-portrait` | 图/文生图 | 竖屏 |
| `gemini-3.0-pro-image-square` | 图/文生图 | 方图 |
| `gemini-3.0-pro-image-four-three` | 图/文生图 | 横屏 4:3 |
| `gemini-3.0-pro-image-three-four` | 图/文生图 | 竖屏 3:4 |
| `gemini-3.0-pro-image-landscape-2k` | 图/文生图(2K) | 横屏 |
| `gemini-3.0-pro-image-portrait-2k` | 图/文生图(2K) | 竖屏 |
| `gemini-3.0-pro-image-square-2k` | 图/文生图(2K) | 方图 |
| `gemini-3.0-pro-image-four-three-2k` | 图/文生图(2K) | 横屏 4:3 |
| `gemini-3.0-pro-image-three-four-2k` | 图/文生图(2K) | 竖屏 3:4 |
| `gemini-3.0-pro-image-landscape-4k` | 图/文生图(4K) | 横屏 |
| `gemini-3.0-pro-image-portrait-4k` | 图/文生图(4K) | 竖屏 |
| `gemini-3.0-pro-image-square-4k` | 图/文生图(4K) | 方图 |
| `gemini-3.0-pro-image-four-three-4k` | 图/文生图(4K) | 横屏 4:3 |
| `gemini-3.0-pro-image-three-four-4k` | 图/文生图(4K) | 竖屏 3:4 |
| `imagen-4.0-generate-preview-landscape` | 图/文生图 | 横屏 |
| `imagen-4.0-generate-preview-portrait` | 图/文生图 | 竖屏 |
| `gemini-3.1-flash-image-landscape` | 图/文生图 | 横屏 |
| `gemini-3.1-flash-image-portrait` | 图/文生图 | 竖屏 |
| `gemini-3.1-flash-image-square` | 图/文生图 | 方图 |
| `gemini-3.1-flash-image-four-three` | 图/文生图 | 横屏 4:3 |
| `gemini-3.1-flash-image-three-four` | 图/文生图 | 竖屏 3:4 |
| `gemini-3.1-flash-image-landscape-2k` | 图/文生图(2K) | 横屏 |
| `gemini-3.1-flash-image-portrait-2k` | 图/文生图(2K) | 竖屏 |
| `gemini-3.1-flash-image-square-2k` | 图/文生图(2K) | 方图 |
| `gemini-3.1-flash-image-four-three-2k` | 图/文生图(2K) | 横屏 4:3 |
| `gemini-3.1-flash-image-three-four-2k` | 图/文生图(2K) | 竖屏 3:4 |
| `gemini-3.1-flash-image-landscape-4k` | 图/文生图(4K) | 横屏 |
| `gemini-3.1-flash-image-portrait-4k` | 图/文生图(4K) | 竖屏 |
| `gemini-3.1-flash-image-square-4k` | 图/文生图(4K) | 方图 |
| `gemini-3.1-flash-image-four-three-4k` | 图/文生图(4K) | 横屏 4:3 |
| `gemini-3.1-flash-image-three-four-4k` | 图/文生图(4K) | 竖屏 3:4 |

### 视频生成

#### 文生视频 (T2V - Text to Video)
⚠️ **不支持上传图片**

| 模型名称 | 说明| 尺寸 |
|---------|---------|--------|
| `veo_3_1_t2v_fast_portrait` | 文生视频 | 竖屏 |
| `veo_3_1_t2v_fast_landscape` | 文生视频 | 横屏 |
| `veo_3_1_t2v_fast_portrait_ultra` | 文生视频 | 竖屏 |
| `veo_3_1_t2v_fast_ultra` | 文生视频 | 横屏 |
| `veo_3_1_t2v_fast_portrait_ultra_relaxed` | 文生视频 | 竖屏 |
| `veo_3_1_t2v_fast_ultra_relaxed` | 文生视频 | 横屏 |
| `veo_3_1_t2v_portrait` | 文生视频 | 竖屏 |
| `veo_3_1_t2v_landscape` | 文生视频 | 横屏 |
| `veo_3_1_t2v_landscape_4s` | 文生视频 4秒 | 横屏 |
| `veo_3_1_t2v_portrait_4s` | 文生视频 4秒 | 竖屏 |
| `veo_3_1_t2v_landscape_6s` | 文生视频 6秒 | 横屏 |
| `veo_3_1_t2v_portrait_6s` | 文生视频 6秒 | 竖屏 |
| `veo_3_1_t2v_fast_landscape_4s` | 文生视频 Fast 4秒 | 横屏 |
| `veo_3_1_t2v_fast_portrait_4s` | 文生视频 Fast 4秒 | 竖屏 |
| `veo_3_1_t2v_fast_landscape_6s` | 文生视频 Fast 6秒 | 横屏 |
| `veo_3_1_t2v_fast_portrait_6s` | 文生视频 Fast 6秒 | 竖屏 |
| `veo_3_1_t2v_lite_portrait` | 文生视频 Lite | 竖屏 |
| `veo_3_1_t2v_lite_landscape` | 文生视频 Lite | 横屏 |
| `veo_3_1_t2v_lite_4s_portrait` | 文生视频 Lite 4秒 | 竖屏 |
| `veo_3_1_t2v_lite_4s_landscape` | 文生视频 Lite 4秒 | 横屏 |
| `veo_3_1_t2v_lite_6s_portrait` | 文生视频 Lite 6秒 | 竖屏 |
| `veo_3_1_t2v_lite_6s_landscape` | 文生视频 Lite 6秒 | 横屏 |

#### 首尾帧模型 (I2V - Image to Video)
📸 **支持1-2张图片：1张作为首帧，2张作为首尾帧**

> 💡 **自动适配**：系统会根据图片数量自动选择对应的 model_key
> - **单帧模式**（1张图）：使用首帧生成视频
> - **双帧模式**（2张图）：使用首帧+尾帧生成过渡视频
> - `veo_3_1_i2v_lite_*` 仅支持 **1 张** 首帧图片
> - `veo_3_1_interpolation_lite_*` 仅支持 **2 张** 首尾帧图片

| 模型名称 | 说明| 尺寸 |
|---------|---------|--------|
| `veo_3_1_i2v_s_fast_portrait_fl` | 图生视频 | 竖屏 |
| `veo_3_1_i2v_s_fast_fl` | 图生视频 | 横屏 |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl` | 图生视频 | 竖屏 |
| `veo_3_1_i2v_s_fast_ultra_fl` | 图生视频 | 横屏 |
| `veo_3_1_i2v_s_fast_portrait_ultra_relaxed` | 图生视频 | 竖屏 |
| `veo_3_1_i2v_s_fast_ultra_relaxed` | 图生视频 | 横屏 |
| `veo_3_1_i2v_s_portrait` | 图生视频 | 竖屏 |
| `veo_3_1_i2v_s_landscape` | 图生视频 | 横屏 |
| `veo_3_1_i2v_s_landscape_4s` | 图生视频 4秒 | 横屏 |
| `veo_3_1_i2v_s_portrait_4s` | 图生视频 4秒 | 竖屏 |
| `veo_3_1_i2v_s_landscape_6s` | 图生视频 6秒 | 横屏 |
| `veo_3_1_i2v_s_portrait_6s` | 图生视频 6秒 | 竖屏 |
| `veo_3_1_i2v_s_fast_landscape_4s_fl` | 图生视频 Fast 4秒 | 横屏 |
| `veo_3_1_i2v_s_fast_portrait_4s_fl` | 图生视频 Fast 4秒 | 竖屏 |
| `veo_3_1_i2v_s_fast_landscape_6s_fl` | 图生视频 Fast 6秒 | 横屏 |
| `veo_3_1_i2v_s_fast_portrait_6s_fl` | 图生视频 Fast 6秒 | 竖屏 |
| `veo_3_1_i2v_lite_portrait` | 图生视频 Lite（仅首帧） | 竖屏 |
| `veo_3_1_i2v_lite_landscape` | 图生视频 Lite（仅首帧） | 横屏 |
| `veo_3_1_i2v_lite_4s_portrait` | 图生视频 Lite 4秒（仅首帧） | 竖屏 |
| `veo_3_1_i2v_lite_4s_landscape` | 图生视频 Lite 4秒（仅首帧） | 横屏 |
| `veo_3_1_i2v_lite_6s_portrait` | 图生视频 Lite 6秒（仅首帧） | 竖屏 |
| `veo_3_1_i2v_lite_6s_landscape` | 图生视频 Lite 6秒（仅首帧） | 横屏 |
| `veo_3_1_interpolation_lite_portrait` | 图生视频 Lite（首尾帧过渡） | 竖屏 |
| `veo_3_1_interpolation_lite_landscape` | 图生视频 Lite（首尾帧过渡） | 横屏 |
| `veo_3_1_interpolation_lite_4s_portrait` | 图生视频 Lite 4秒（首尾帧过渡） | 竖屏 |
| `veo_3_1_interpolation_lite_4s_landscape` | 图生视频 Lite 4秒（首尾帧过渡） | 横屏 |
| `veo_3_1_interpolation_lite_6s_portrait` | 图生视频 Lite 6秒（首尾帧过渡） | 竖屏 |
| `veo_3_1_interpolation_lite_6s_landscape` | 图生视频 Lite 6秒（首尾帧过渡） | 横屏 |

#### 多图生成 (R2V - Reference Images to Video)
🖼️ **支持多张图片**

> **2026-03-06 更新**
>
> - 已同步上游新版 `R2V` 视频请求体
> - `textInput` 已切换为 `structuredPrompt.parts`
> - 顶层新增 `mediaGenerationContext.batchId`
> - 顶层新增 `useV2ModelConfig: true`
> - 横屏 / 竖屏 `R2V` 模型共用同一套新版请求体
> - 横屏 `R2V` 的上游 `videoModelKey` 已切换为 `*_landscape` 形式
> - 根据当前上游协议，`referenceImages` 当前最多传 **3 张**

| 模型名称 | 说明| 尺寸 |
|---------|---------|--------|
| `veo_3_1_r2v_fast_portrait` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape` | 图生视频 | 横屏 |
| `veo_3_1_r2v_fast_portrait_ultra` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape_ultra` | 图生视频 | 横屏 |
| `veo_3_1_r2v_fast_portrait_ultra_relaxed` | 图生视频 | 竖屏 |
| `veo_3_1_r2v_fast_landscape_ultra_relaxed` | 图生视频 | 横屏 |

#### 视频放大模型 (Upsample)

这些模型不是直接调用上游 upsampler key，而是先用对应的 Veo 3.1 普通模型生成视频，再提交 1080P/4K 放大请求。

| 模型名称 | 说明 | 输出 |
|---------|---------|--------|
| `veo_3_1_t2v_landscape_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_portrait_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_landscape_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_portrait_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_landscape_4s_4k` | 文生视频 4秒放大 | 4K |
| `veo_3_1_t2v_portrait_4s_4k` | 文生视频 4秒放大 | 4K |
| `veo_3_1_t2v_landscape_4s_1080p` | 文生视频 4秒放大 | 1080P |
| `veo_3_1_t2v_portrait_4s_1080p` | 文生视频 4秒放大 | 1080P |
| `veo_3_1_t2v_landscape_6s_4k` | 文生视频 6秒放大 | 4K |
| `veo_3_1_t2v_portrait_6s_4k` | 文生视频 6秒放大 | 4K |
| `veo_3_1_t2v_landscape_6s_1080p` | 文生视频 6秒放大 | 1080P |
| `veo_3_1_t2v_portrait_6s_1080p` | 文生视频 6秒放大 | 1080P |
| `veo_3_1_t2v_fast_portrait_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_portrait_ultra_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_ultra_4k` | 文生视频放大 | 4K |
| `veo_3_1_t2v_fast_portrait_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_portrait_ultra_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_t2v_fast_ultra_1080p` | 文生视频放大 | 1080P |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_fast_ultra_fl_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_fast_ultra_fl_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_landscape_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_portrait_4k` | 图生视频放大 | 4K |
| `veo_3_1_i2v_s_landscape_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_portrait_1080p` | 图生视频放大 | 1080P |
| `veo_3_1_i2v_s_landscape_4s_4k` | 图生视频 4秒放大 | 4K |
| `veo_3_1_i2v_s_portrait_4s_4k` | 图生视频 4秒放大 | 4K |
| `veo_3_1_i2v_s_landscape_4s_1080p` | 图生视频 4秒放大 | 1080P |
| `veo_3_1_i2v_s_portrait_4s_1080p` | 图生视频 4秒放大 | 1080P |
| `veo_3_1_i2v_s_landscape_6s_4k` | 图生视频 6秒放大 | 4K |
| `veo_3_1_i2v_s_portrait_6s_4k` | 图生视频 6秒放大 | 4K |
| `veo_3_1_i2v_s_landscape_6s_1080p` | 图生视频 6秒放大 | 1080P |
| `veo_3_1_i2v_s_portrait_6s_1080p` | 图生视频 6秒放大 | 1080P |
| `veo_3_1_r2v_fast_portrait_ultra_4k` | 多图视频放大 | 4K |
| `veo_3_1_r2v_fast_landscape_ultra_4k` | 多图视频放大 | 4K |
| `veo_3_1_r2v_fast_portrait_ultra_1080p` | 多图视频放大 | 1080P |
| `veo_3_1_r2v_fast_landscape_ultra_1080p` | 多图视频放大 | 1080P |

## 📡 API 使用示例（需要使用流式）

> 除了下方 `OpenAI-compatible` 示例，服务也支持 Gemini 官方格式：
> - `POST /v1beta/models/{model}:generateContent`
> - `POST /models/{model}:generateContent`
> - `POST /v1beta/models/{model}:streamGenerateContent`
> - `POST /models/{model}:streamGenerateContent`
>
> Gemini 官方格式支持以下认证方式：
> - `Authorization: Bearer <api_key>`
> - `x-goog-api-key: <api_key>`
> - `?key=<api_key>`
>
> Gemini 官方图片请求体已兼容：
> - `systemInstruction`
> - `contents[].parts[].text`
> - `contents[].parts[].inlineData`
> - `contents[].parts[].fileData.fileUri`
> - `generationConfig.responseModalities`
> - `generationConfig.imageConfig.aspectRatio`
> - `generationConfig.imageConfig.imageSize`
> - `project_id`（可选；传入已存在的真实 project ID 或客户端别名时直接复用；首次传入新值时自动创建并绑定，不传则按现有 Token/project 池逻辑）

### Gemini 官方 generateContent（文生图）

> 已使用真实 Token 实测通过。
> 如需流式返回，可将路径替换为 `:streamGenerateContent?alt=sse`。

```bash
curl -X POST "http://localhost:8000/models/gemini-3.1-flash-image:generateContent" \
  -H "x-goog-api-key: han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project_id_or_client_alias>",
    "systemInstruction": {
      "parts": [
        {
          "text": "Return an image only."
        }
      ]
    },
    "contents": [
      {
        "role": "user",
        "parts": [
          {
            "text": "一颗放在木桌上的红苹果，棚拍光线，极简背景"
          }
        ]
      }
    ],
    "generationConfig": {
      "responseModalities": ["IMAGE"],
      "imageConfig": {
        "aspectRatio": "1:1",
        "imageSize": "1K"
      }
    }
  }'
```

### 文生图

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image-landscape",
    "project_id": "<project_id_or_client_alias>",
    "messages": [
      {
        "role": "user",
        "content": "一只可爱的猫咪在花园里玩耍"
      }
    ],
    "stream": true
  }'
```

### 图生图

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image-landscape",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "将这张图片变成水彩画风格"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<base64_encoded_image>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

### 文生视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_t2v_fast_landscape",
    "messages": [
      {
        "role": "user",
        "content": "一只小猫在草地上追逐蝴蝶"
      }
    ],
    "stream": true
  }'
```

### 首尾帧生成视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_i2v_s_fast_fl_landscape",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "从第一张图过渡到第二张图"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<首帧base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,<尾帧base64>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

### 多图生成视频

> `R2V` 会由服务端自动组装新版视频请求体，调用方仍然使用 OpenAI 兼容输入即可。
> 服务端会将横屏 `R2V` 自动映射到最新的 `*_landscape` 上游模型键。
> 当前最多传 **3 张参考图**。

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer han1234" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3_1_r2v_fast_portrait",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "以三张参考图的人物和场景为基础，生成一段镜头平滑推进的竖屏视频"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图1base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图2base64>"
            }
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64/<参考图3base64>"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

---

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

---

## 🙏 致谢

- [PearNoDec](https://github.com/PearNoDec) 提供的YesCaptcha打码方案
- [raomaiping](https://github.com/raomaiping) 提供的无头打码方案
感谢所有贡献者和使用者的支持！

---

## 📞 联系方式

- 提交 Issue：[GitHub Issues](https://github.com/TheSmallHanCat/flow2api/issues)

---

**⭐ 如果这个项目对你有帮助，请给个 Star！**

## 最近更新

- `9f1d712` 同步 personal 打码逻辑，包含清理、浏览器参数和打码方式配置。
- `da2ad06` 合并 PR #133。
- `abd0c00` 修复 PR #133 合并后的集成问题。
- `55431c9` 将 origin/main 同步到 PR #133。
- `4b7a0ad` 新增 Prometheus 服务指标和 Token 健康监控。

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=TheSmallHanCat/flow2api&type=date&legend=top-left)](https://star-history.dera.page/#TheSmallHanCat/flow2api&type=date&legend=top-left)
