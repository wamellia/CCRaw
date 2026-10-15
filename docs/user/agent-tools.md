# Agent 工具与 MCP

Photo Agent 对话和 MCP 使用同一套工具定义、参数校验及 Photo Engine。工具创建可检查的编辑方案；在界面确认后才从原片计算派生版本。

## 界面使用

1. 导入并扫描照片，选中要调整的照片。一次批量方案最多覆盖 30 张。
2. 输入指令，例如「曝光统一设为 0.3 档，保留每张照片的裁切」或「裁切右半部分，顺时针旋转 90 度，再预览」。
3. 在「编辑方案」选中方案，点击「预览方案」查看当前调整与方案效果。选择多张照片时，列表只展示这些照片的待选方案。
4. 点击「查看并应用方案」，检查参数并确认，生成独立的全分辨率 16 位 TIFF。

未配置 LLM 时，可以使用「推荐修图」和「预览方案」。自动选区、修复和堆栈通过「照片编辑」进入完整编辑器。

## 工具

| 名称 | 作用 |
|---|---|
| `album_report` | 本工程重复、相似、质量、事件与匿名人物簇建议 |
| `search_photos` | 受约束的元数据及本地语义检索，返回照片 ID |
| `diagnose_photo` | 选中照片的本地诊断 |
| `recommend_edits` | 为一张选中照片创建最多三种本地方案 |
| `get_edit_controls` | 查询一个参数分区的类型、范围与当前值；不传照片 ID 时返回默认值 |
| `propose_adjustment` | 为一张选中照片创建参数调整方案 |
| `propose_batch_adjustment` | 用同一 patch 为 1–30 张选中照片创建方案，保留各自未修改的字段 |
| `preview_edit` | 渲染待确认本地方案的前后效果；MCP 同时返回两张 JPEG 图像 |
| `propose_external_edit` | 准备外部编辑方案与上传、Provider、费用披露，等待界面确认 |
| `query_catalog` | 按相册、白名单规则和稳定排序分页查询；每页最多 200 张 |
| `set_photo_state` | 设置选中照片的星级、保留/拒绝和颜色标签 |
| `edit_photo_metadata` | 编辑选中照片在工程内的元数据 |
| `create_album` | 创建普通相册、文件夹或智能相册 |
| `album_members` | 将选中照片加入或移出普通相册 |
| `create_virtual_copy` | 为选中照片创建独立编辑的虚拟副本 |
| `create_photo_stack` | 将选中照片组成堆栈 |
| `catalog_history` | 撤销或重做上一条相册管理命令 |

方案创建不上传照片。`propose_external_edit` 会在本地准备经过当前配方处理的参考图；实际服务请求仍由界面确认后执行。

## 参数

先调用 `get_edit_controls`，以返回的 schema 为准。支持以下分区：

- `adjustments`：曝光、对比度、白平衡、亮暗部、饱和度、清晰度、降噪等现有全局调整。
- `effects`、`hsl`、`grading`：暗角与颗粒、八组颜色及三段色彩分级。
- `curves`：`RGB`、`R`、`G`、`B` 通道，控制点为 `[x, y]`，坐标 0–1，x 严格递增。兼容旧 `curve` 的 0–255 点，但不能与 `curves.RGB` 同时指定。
- `crop`：`[x0, y0, x1, y1]`，坐标 0–1；`null` 清除裁切。
- `rotation`：顺时针 90° 的次数，整数 0–3，表示目标旋转状态。
- `straighten`：拉直角度，范围 −15° 至 15°。
- `monochrome`、`reset`：布尔值。`reset=true` 先恢复默认配方，再叠加本次参数。
- `masks`：新建线性、径向、亮度范围或画笔蒙版；也可按 `index` 修改已有蒙版的调整参数。
- `professional`：光学、几何、相机校准、细节、八色黑白、点颜色和红眼；先查询 schema 再设置。模型不能指定任意配置文件路径。
- `process`：兼容显影为 `1`，浮点 Rec.2020 宽色域显影为 `2`。

参数表示目标值，不是相对增量。裁切和蒙版使用**原片未旋转的归一化坐标**。画笔半径为原片短边的相对长度，范围 0.001–0.5。每次最多添加八个蒙版，配方共最多 32 个；一个 patch 最多 64 KiB。未知字段、非有限数值、越界参数、重复 x 的曲线以及与蒙版种类不匹配的字段都会报错，原来的待选方案保留。

新建蒙版使用以下种类专属字段；`kind`、`name`、`enabled`、`invert`、`opacity`、`adjustments` 为共有字段：

| `kind` | 几何 / 范围字段 |
|---|---|
| `linear` | `start`、`end`，两点不能相同 |
| `radial` | `start`、`end` 为椭圆包围框；可设置 `feather` |
| `luminance` | `luminance_range` 为 0–100 的有序区间，`range_falloff` 为 0–100 |
| `brush` | `strokes` 至少一条；每条含 `points`、`radius` 和可选 `erase`；可设置 `feather` |

新建蒙版省略几何或范围字段时采用现有 Photo Engine 默认值，画笔必须提供笔划。简便 `region` 支持 `top`、`bottom`、`center`、`edges`，只与 `adjustments` 一同使用；`index` 同样只与 `adjustments` 使用。

`components` 可包含最多 64 个有序的几何、亮度或画笔组件，每个包含 `op`（`add`、`subtract` 或 `intersect`）和 `mask`。父蒙版不透明度作用于完整组合。Agent patch 只接收叶组件，界面配方支持有界嵌套。

创建方案的示例：

```json
{
  "photo_id": "替换为工程照片ID",
  "patch": {
    "adjustments": {"exposure": 0.3},
    "crop": [0.5, 0, 1, 1],
    "curves": {"R": [[0, 0], [0.5, 0.56], [1, 1]]},
    "masks": [{
      "kind": "radial",
      "start": [0.6, 0.2],
      "end": [0.9, 0.8],
      "feather": 70,
      "adjustments": {"exposure": 0.2}
    }]
  }
}
```

预览使用返回的方案 ID：

```json
{"proposal_id": "替换为方案ID", "max_edge": 1024}
```

最长边可设为 256–2048 像素。预览使用当前配方与方案配方，分别保持宽高比例；它是 8 位 JPEG 效果图，不是全分辨率输出。相同输入复用缓存；原片改变或方案失效时拒绝预览。

软件中的预览窗口另有「加载原像素对比」：后台读取原片，用两份独立配方渲染可见区域；支持同步缩放和移动，100% 按实际设备像素显示。它保留方案待确认状态，不产生派生版本。MCP 的图像结果仍为有尺寸上限的 JPEG。

## MCP 连接

MCP 通过 Python 源码或已安装包的控制台入口启动，不能用无控制台的 Windows GUI exe 代替。先安装可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/agent.txt
```

在 MCP 客户端设置启动程序和参数。例如：

```json
{
  "mcpServers": {
    "ccraw": {
      "command": "C:\\apps\\ccraw\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\apps\\ccraw\\main.py",
        "--agent-mcp",
        "C:\\photos\\album.ccrawagent",
        "--select", "替换为允许编辑的照片ID"
      ]
    }
  }
}
```

将路径和照片 ID 换成实际值。界面选中照片后，右键「复制选中照片 ID」可取得 ID；也可以先通过 `search_photos` 检索。授权多张照片时重复 `--select`，最多 30 张。启动时不指定 `--select` 仍可查看报告、找图及查询默认参数，不能创建或预览照片编辑方案。修改授权集合后重启该 MCP 服务。

命令等价形式：

```powershell
.\.venv\Scripts\python.exe main.py --agent-mcp "C:\photos\album.ccrawagent" --select PHOTO_ID
```

这条命令由 MCP 客户端管理 stdin/stdout；直接在终端启动会等待协议请求。服务不监听网络端口，同一服务中的工具请求串行处理。预览结果包含结构化描述和两张 `image/jpeg` 图像块，顺序为当前调整、方案效果。客户端需支持图像工具结果才能显示图片。

MCP 创建的方案在软件中可见；工程已打开时重新选择对应照片，即可刷新方案列表。MCP 不提供应用、导出、删除原片或执行付费请求的工具。执行记录只供回看，不会重放请求。内置 Agent 对话的预览工具返回 JSON 并在软件显示图片，不会自动把预览上传到对话模型。

## English quick start

Install `requirements/agent.txt`, then configure your MCP client to run the source environment's Python executable with `main.py --agent-mcp PROJECT.ccrawagent`. Repeat `--select PHOTO_ID` to authorize up to 30 photos for proposal creation and previews. Get IDs from search results or the Agent gallery's **Copy Selected Photo IDs** context-menu action. Without authorized IDs, the server still supports reports, searches and default control discovery.

The same seventeen tools serve the in-app Agent and MCP, including eight catalog commands with persistent undo/redo. Call `get_edit_controls` to discover section schemas and current values, then `propose_adjustment` or `propose_batch_adjustment`. Patches set absolute values, preserve untouched per-photo settings, and reject unknown or invalid fields. Professional controls include optics, geometry, calibration, detail, point color, monochrome mixing and red-eye correction. Crops and mask points use normalized **unrotated original-image coordinates**. Rotation is the target number of clockwise quarter-turns (0–3); straightening is −15° to 15°. Channel curve coordinates are 0–1 with strictly increasing x values. Geometric masks support linear gradients, radial gradients, luminance ranges, brushes and bounded set-operation components; AI selections remain available in the full editor.

Call `preview_edit` with the returned proposal ID and optional `max_edge` (256–2048; default 1024). MCP returns structured text followed by two JPEG image blocks: current adjustments, then the proposed result. These are bounded 8-bit previews, not full-resolution output. In-app chat receives JSON and displays the comparison locally; it does not automatically upload the preview to the conversation provider.

Select the proposal in the Agent workspace, click **Preview Proposal**, then **Review and Apply Proposal** to approve a separate full-resolution 16-bit TIFF. Creating or previewing a proposal leaves the original intact and creates no derivative. Batch creation validates all photos before replacing pending proposals in one transaction. The MCP server uses stdio with serialized requests and has no apply, export, original-deletion or paid-execution tool. Use Python source or a console entry point rather than the windowed GUI executable.
