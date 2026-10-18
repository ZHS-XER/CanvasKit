---
name: canvaskit
description: 获取上海交大 Canvas（oc.sjtu.edu.cn）的课程资料：课堂录播的语音识别字幕、课堂投影截图（含 OCR、时间点、幻灯片页码）、课程文件（课件、参考书等）。当用户提到交大课程的录播、字幕、老师讲了什么、上到哪一页、课件 / PPT / 讲义下载、Canvas 或 oc.sjtu 时使用。不负责作业提交、成绩、讨论区。
---

# canvaskit

`canvaskit` 是一个已安装的命令行工具，凭 Canvas Access Token 访问交大 Canvas 和录播平台（v.sjtu.edu.cn）。

## 调用约定

- 始终加 `--json`：结果是 stdout 上的 JSON，进度和警告在 stderr，解析时只读 stdout。
- `<course>` 可以是 `config.toml` 里的课程名（如 `高数`）、课程 id，或课程名 / 代码的一部分。匹配到多门课时会报错并列出候选，改用 id 即可。
- 输出已存在时默认跳过（`status: "exists"`），重复运行是安全的；只有用户要求刷新时才加 `--force`。
- 退出码：`0` 成功；`1` 请求失败；`2` 没有 token、配置文件有误或参数错误。
- 不知道参数时运行 `canvaskit <command> --help`。

## 课程配置

`~/.config/canvaskit/config.toml` 把课程名对应到 Canvas 课程和本地文件夹。`canvaskit courses --json` 的 `config_key` / `dir` 字段显示哪些课已配置。

```toml
[courses."高数"]          # 中文课程名必须加引号
canvas_id = 12345
dir = "~/Documents/高等数学"
subtitles = "课程字幕"     # 可选，默认 "subtitles"
slides = "slides"          # 可选，默认 "slides"，课程文件平铺在这里
subtitle_format = "srt,txt" # 可选，默认 "srt,txt"
```

已配置的课程：字幕和课堂截图默认写到 `<dir>/<subtitles>/`，课程文件默认下载到 `<dir>/<slides>/`，不用写 `-o`。
未配置的课程必须用 `-o` 指定目录；用户没说放哪时写到 `/tmp/canvaskit/<课程>/`，不要自作主张写进用户的文件夹。

## 日常：`sync`

```bash
canvaskit sync --json            # 所有已配置的课程
canvaskit sync 高数 --json
```

- 自动下载所有还没有的字幕。
- 课程文件**只列出不下载**：`new_files`（上次 sync 之后新出现的）、`updated_files`（下载过但老师替换了内容的）。每个文件带 `source`：`单元：<单元名>`（老师按章节挂在主页的，通常是课件）、`文件库`（往年旧版、杂项等）、`链接：<页面>`（只能从大纲、作业说明等页面里的链接找到的隐藏文件）。
- 把新文件按来源整理后告诉用户，问要下载哪些，再执行下载。不要未经同意就批量下载。
- 第一次 sync 会扫描课程文件夹：同名且大小相同的文件记为已有（`found_locally`），不会列为新文件。
- 某门课出错时，报告里会带 `subtitles_error`（录播平台出错）或 `files_error`（取文件列表出错），另一部分照常返回；整门课都失败时只有 `error`。

## 课程文件

```bash
canvaskit files 高数 --json                  # 所有文件及状态；--pending 只看还没有的；-m 关键词
canvaskit download 高数 --new --json         # 下载最近一次 sync 列出的新文件 / 更新
canvaskit download 高数 --id 123456 --id 123457 --json
canvaskit download 高数 -m 第三章 --dry-run --json
canvaskit download 高数 --all --dry-run --json   # 所有还没有的；可能很多，先 dry-run
```

- `download` 必须用 `--id` / `--new` / `-m` / `--since` / `--all` 指定文件。
- 文件平铺下载到 slides 文件夹；重名时文件名后加 ` (id)`，不会覆盖用户自己的文件。
- 按 Canvas 文件 id 记录下载过的文件（`~/.config/canvaskit/state/`），用户挪走或改名后不会重复下载。
- `files` 的 `status`：`new` 新 / `pending` 列出过但没下载 / `downloaded` 已下载 / `local` 本地已有 / `updated` 有更新 / `locked` 老师还没开放。
- `download` 的 `status`：`downloaded` / `exists` / `locked` / `error` / `would-download`。

## 录播

```bash
canvaskit lectures 高数 --json               # 录播列表，按时间从旧到新
```

### 字幕：老师讲了什么

```bash
canvaskit subtitles 高数 --latest 1 --format txt --json
canvaskit subtitles 高数 --date today --format txt --json
canvaskit subtitles 高数 --since 2026-10-01 --json
canvaskit subtitles 高数 --id 654321 --format txt,srt --json
```

- 自己读用 `txt`：合并成段落，每段带 `[HH:MM:SS]` 视频偏移，比 `srt` 省很多 token。
- `status`：`downloaded` / `exists` / `unavailable`（平台还没生成字幕）/ `error`。刚下课的录播可能还没有字幕，平台报错时也会是 `error`。

### 课堂截图：仅在用户要求时使用

平台在投影画面变化时自动截图。判断讲课内容和进度只用字幕，不要主动去取截图；用户明确想看课堂画面、PPT 截图时才用。

```bash
canvaskit snapshots 高数 --latest 1 --brief --json   # 精简摘要 .slides.brief.md，每张一行
canvaskit snapshots 高数 --latest 1 --json           # 完整 OCR：.slides.md 和 .slides.json
canvaskit snapshots 高数 --latest 1 --images --json  # 同时下载 JPEG，需要看图时用
```

已配置的课程写到字幕文件夹，未配置的课程用 `-o` 指定目录（规则同上）。`status` 与字幕相同；`--images` 时部分图片下载失败会记为 `error`（已写出的文件保留），重新运行同一条命令只补下缺的图片。

`--brief` 每行是 `[视频内偏移] 类型 | 关键词 | 开头文字`，连续重复的截图会合并（`×N`），编辑视图一行会带讲稿名和“第N/M张”。一般读这个就够了。

`.slides.json` 字段：

| 字段 | 含义 |
|---|---|
| `seek_seconds` / `at` | 视频内偏移秒数 / 实际时间 |
| `kind` | `slideshow` 全屏放映；`editor` PowerPoint 编辑视图；`other` 桌面、浏览器、Word 等 |
| `deck` / `slide_no` / `slide_total` | 仅 `editor`：标题栏里的讲稿名、状态栏里的“幻灯片第N张，共M张” |
| `ocr_text` / `keywords` | OCR 文字和关键词 |

## 课后：这节课讲了什么、讲到哪

只用字幕，定位到大概位置即可。

1. `canvaskit sync <课程> --json`，拿到最新一节的字幕（读 `txt`）。
2. 通读字幕，按时间点归纳讲了哪些主题、例题，以及老师强调的事项（作业、考试、点名等）。
3. 对照本地课件的目录或章节标题（课件在配置的课程文件夹里，`slides/` 下是从 Canvas 下载的），说明大概讲到哪份课件的哪一部分。只在需要时才翻具体页面。

## 解读结果时注意

- 字幕是自动语音识别，错字很多，数字（题号、页码、公式里的数）尤其不可靠；开头结尾常有课前课后的闲聊。引用时说明可能有误，关键信息与课件交叉核对。
- `deck` 讲稿名带 OCR 错字，要和课件文件名模糊匹配。老师 PowerPoint 里的页码含隐藏页，和 Canvas 上导出的 PDF 页码可能不一致；老师用的版本也可能比用户本地的新，先 `sync` 看有没有新版课件。
- 截图和 OCR 中可能有老师屏幕上的个人信息（登录页、学生名单等），不要转发、不要写进会公开的地方。
- 文件名里的“第N节”是当天这门课的第几次录播，不是全校统一的节次。

## 更新工具

用户要求更新 canvaskit 时：`uv tool upgrade canvaskit`，然后 `canvaskit skill install --force` 同步新版说明。

## 出错时

- `Canvas token not found`：让用户在 Canvas → 账户 → 设置 → “新建访问许可证”生成 token，写入 `~/.config/canvaskit/env`（`export CANVAS_TOKEN=...`，权限 600）。不要让用户把 token 贴到对话里。
- `whoami` 报 `-> 401`：token 失效或被撤销，让用户重新生成。
- `state/<id>.json is corrupt`：文件清单损坏，删掉该文件后重新 `sync` 即可（本地已有的文件会按同名同大小重新认出来）。
- `No courses configured` / `is not a configured course`：在 `config.toml` 里添加课程（见上）。
- `config.toml: Invalid initial character for a key part`：中文课程名没加引号。
- `No course matches` / `matches several courses`：先 `canvaskit courses --all --json` 找到课程 id。
- `当前课程暂未安排直录播` / `no lecture-recording tool` / `did not return a teaching class`：这门课没有录播，只能用 `files` / `download`。
- `LTI launch ...` / `Unexpected response` 或其他 `Video platform error`：录播平台登录或接口出错，可能是学校改版导致失效。如实告诉用户，不要反复重试。
