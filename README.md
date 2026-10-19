# CanvasKit

[![CI](https://github.com/ZHS-XER/CanvasKit/actions/workflows/ci.yml/badge.svg)](https://github.com/ZHS-XER/CanvasKit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

CanvasKit 是面向 Agent（如 Claude Code）的上海交通大学 Canvas（`oc.sjtu.edu.cn`）工具，用于获取课堂录播字幕和课程文件。配合 Agent 使用，可以在课后总结授课内容、定位授课进度、检查并下载新发布的课件。

仅需 Canvas 访问令牌（Token），无需 jAccount 登录。

## 快速开始

### 1. 安装

将以下指令发送给 Agent：

> 请阅读 https://github.com/ZHS-XER/CanvasKit 的 README，按照“命令行使用 → 安装”一节安装 CanvasKit 及其 Claude Code Skill，完成后运行 `canvaskit --version` 确认安装成功。

### 2. 配置 Token

**生成 Token**

1. 登录 [oc.sjtu.edu.cn](https://oc.sjtu.edu.cn)，点击左侧导航栏的“账户”，进入“设置”。
2. 在“已批准的集成”部分点击“新建访问许可证”，填写用途后生成。
3. 复制生成的 Token。该 Token 仅显示一次。

**保存 Token**

在终端中执行以下命令，按提示粘贴 Token 并回车（输入内容不会显示）。

macOS / Linux：

```bash
mkdir -p ~/.config/canvaskit
echo -n "请粘贴 Token 并回车: "; read -rs t; echo "export CANVAS_TOKEN=$t" > ~/.config/canvaskit/env; unset t
chmod 600 ~/.config/canvaskit/env
```

Windows（PowerShell）：

```powershell
New-Item -ItemType Directory -Force "$HOME\.config\canvaskit" | Out-Null
$s = Read-Host "请粘贴 Token 并回车" -AsSecureString
$t = [Net.NetworkCredential]::new("", $s).Password
Set-Content -Path "$HOME\.config\canvaskit\env" -Value "CANVAS_TOKEN=$t" -Encoding ascii
Remove-Variable s, t
```

Token 具有与账号相同的访问权限，请勿泄露，也不要在与 Agent 的对话中直接发送。

**验证**：向 Agent 发送“检查 CanvasKit 是否可用”，Agent 返回你的 Canvas 用户名即表示配置成功。

### 3. 关联课程与本地文件夹（可选）

向 Agent 发送“帮我配置 CanvasKit”。Agent 会列出本学期的课程，询问要关联哪几门、文件放在哪里：

- **已有课程文件夹**：告诉 Agent 每门课对应的文件夹。文件夹中已有的课件会被识别，不会重复下载。
- **从头新建**：告诉 Agent 一个根目录（如 `~/Documents/Canvas`），每门课自动建在 `<根目录>/<课程名>` 下。

也可以直接说明对应关系，例如：

> 将“高等数学”关联到 `~/Documents/高等数学`，字幕保存在其中的“课程字幕”文件夹。

关联后，字幕与课件将自动保存到对应文件夹，同步时也无需再指定课程。

## 使用方式

安装完成后，直接用自然语言向 Agent 提出需求即可。以下为常用示例。

### 授课内容

- **课后总结**：“总结今天的高等数学课。”\
  下载最新一节课的字幕，概括授课内容，并对照本地课件说明讲授位置。
- **授课进度**：“大学物理目前讲到哪里了？”\
  根据最近的字幕判断进度，指出对应的课件及章节。
- **课堂通知**：“本周老师是否布置了作业或提到考试安排？”\
  从字幕中提取作业、考试、考勤等相关内容，并标注时间点。
- **课堂截图**：“查看上节课的 PPT 截图。”\
  获取录播平台自动截取的投影画面。

### 课程文件

- **检查更新**：“Canvas 上是否有新课件？”\
  同步所有已关联的课程，列出新增和更新的文件，由你确认后再下载。
- **下载文件**：“下载第三章的课件。”\
  下载到课程文件夹中的 `slides/` 目录。
- **查看缺失**：“大学物理还有哪些文件未下载？”\
  列出所有尚未下载的文件及其来源（章节单元、文件库、大纲链接等）。
- **下载字幕**：“下载高等数学本周的字幕。”\
  保存为 SRT 格式（供播放器使用）和 TXT 格式（按段落整理，便于 Agent 阅读）。

### 配置

- **添加课程**：“将线性代数添加到 CanvasKit。”\
  查询课程信息并写入配置文件。

目前不支持作业截止提醒、公告提醒、作业提交和成绩查询。

## 注意事项

- **准确性**：字幕由语音识别自动生成，存在识别错误，数字（如题号、页码）尤其容易出错。重要信息请与课件核对。
- **隐私**：课堂截图可能包含教师屏幕上的个人信息（如登录页面、学生名单），请勿传播下载的内容。
- **稳定性**：录播相关功能依赖交大视频平台的非公开接口，平台改版后可能失效；课程与文件功能基于 Canvas 标准 API，较为稳定。

## 免责声明

- 本项目为个人开发的非官方工具，与上海交通大学及 Canvas（Instructure）无任何关联。
- 本工具仅使用你本人的 Canvas 凭证，访问你本人有权访问的内容，请在遵守学校相关规定的前提下使用。
- 课件、录播、字幕等课程资料的版权归原作者所有，仅供个人学习使用，请勿传播或用于商业用途。
- 本工具按“现状”提供，不对其可用性、准确性及使用后果作任何保证。

---

## 命令行使用

本节面向希望直接使用命令行或了解实现细节的用户，Agent 安装时也依照本节操作。

### 安装

需要 [uv](https://docs.astral.sh/uv/)：macOS 可通过 `brew install uv` 安装，Windows 可通过 `winget install --id=astral-sh.uv -e` 安装。

```bash
uv tool install git+https://github.com/ZHS-XER/CanvasKit
canvaskit skill install        # 将 Skill 安装到 ~/.claude/skills/canvaskit/
canvaskit --version
```

其他 Agent 可通过 `canvaskit skill path` 获取 SKILL.md 的位置，作为工具说明使用。

Windows 上如果安装后提示找不到 `canvaskit` 命令，运行 `uv tool update-shell` 并重新打开终端。

### 升级与卸载

```bash
uv tool upgrade canvaskit
canvaskit skill install --force   # 同步新版 Skill

canvaskit skill uninstall
uv tool uninstall canvaskit
```

卸载工具不会删除 `~/.config/canvaskit/` 中的 Token、配置与下载记录，如不再使用请手动删除。

### 凭证

依次读取环境变量 `CANVAS_TOKEN` 和 `~/.config/canvaskit/env`（格式为 `export CANVAS_TOKEN=...` 或 `CANVAS_TOKEN=...`；Windows 上即 `%USERPROFILE%\.config\canvaskit\env`）。
可通过 `CANVAS_BASE_URL` 指定 Canvas 地址，默认为 `https://oc.sjtu.edu.cn`。

### 课程配置

配置文件位于 `~/.config/canvaskit/config.toml`，`canvas_id` 可通过 `canvaskit courses` 查询：

```toml
[courses."高数"]                 # 中文名称须加引号
canvas_id = 12345
dir = "~/Documents/高等数学"
subtitles = "课程字幕"            # 字幕和课堂截图子目录，默认 subtitles
slides = "slides"                # 课程文件子目录，默认 slides
subtitle_format = "srt,txt"      # 字幕格式，默认 srt,txt
```

### 命令

`<课程>` 可以是配置中的名称、Canvas 课程 ID 或课程名称的一部分。

| 命令 | 说明 |
|---|---|
| `canvaskit whoami` | 验证 Token |
| `canvaskit courses [--all]` | 列出课程，并标注已配置的课程 |
| `canvaskit lectures <课程>` | 列出录播 |
| `canvaskit sync [<课程>...]` | 下载新字幕，列出新增和更新的文件（不下载文件） |
| `canvaskit files <课程> [--pending] [-m <关键词>]` | 列出课程文件及其来源、状态 |
| `canvaskit download <课程> <选择条件> [--dry-run]` | 下载文件，选择条件为 `--new`、`--id`、`-m`、`--since` 或 `--all` |
| `canvaskit subtitles <课程> [<选择条件>] [--format srt,txt]` | 下载字幕，选择条件为 `--latest`、`--date`、`--since` 或 `--id` |
| `canvaskit snapshots <课程> [-o <目录>] [--brief] [--images]` | 获取课堂截图：摘要、完整 OCR 或图片 |
| `canvaskit skill install / uninstall / path` | 安装、卸载 Claude Code Skill，或显示 SKILL.md 位置 |

示例：

```bash
canvaskit sync
canvaskit download 高数 --new
canvaskit download 高数 -m 第三章 --dry-run
canvaskit subtitles 高数 --latest 1 --format txt
```

各命令的完整参数请参阅 `canvaskit <命令> --help`。

### 行为说明

- **输出**：所有命令支持 `--json`。结果输出到 stdout，进度与警告输出到 stderr。退出码 `0` 表示成功，`1` 表示请求失败，`2` 表示凭证、配置或参数错误。
- **幂等**：已存在的输出会被跳过（使用 `--force` 覆盖），重复执行只获取新内容。
- **字幕命名**：如 `高等数学 第5周 周三 第2节.srt`，其中“第N节”指当天该课程的第几次录播。
- **文件来源**：Canvas 中文件统一存放于“文件”页；“单元”是教师按章节挂载的文件；此外还有仅在大纲、作业说明等页面中以链接形式出现的隐藏文件。`sync` 与 `files` 会合并三处来源并去重，同时标注来源。
- **新文件**：指上次 `sync` 之后新出现的文件，已列出的文件不会重复提示；尚未下载的文件可通过 `files --pending` 查看。教师替换文件内容后，该文件标记为“有更新”。
- **下载**：文件平铺保存至 slides 目录，并按 Canvas 文件 ID 记录于 `~/.config/canvaskit/state/`，移动或重命名后不会重复下载。文件重名时追加 ` (ID)` 后缀，不会覆盖已有文件。首次同步时，课程文件夹中同名且大小一致的文件视为已有。

### 实现原理

- **课程与文件**：通过 Canvas REST API 访问，使用 `Authorization: Bearer <token>` 认证。
- **录播**：通过 Canvas 的 `sessionless_launch` 接口启动“课堂视频新版”工具，自动完成 LTI 认证以获取视频平台凭证，进而获取录播列表、字幕与截图。协议细节参考了 [SJTU-Canvas-Helper](https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper)。

## 开发

参见 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)
