"""Central message catalog and live localization for legacy and new Qt widgets.

Widget source text is retained separately from display text. Combo item data,
recipes, filenames, editable inputs and chat contents never become translated IDs.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QTranslator, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QTabBar,
    QWidget,
    QApplication,
    QDialogButtonBox,
    QGroupBox,
    QTreeWidget,
    QTreeWidgetItemIterator,
)
from PySide6.QtGui import QAction

_PAIRS = """
设置|Settings
任务|Tasks
窗口|Window
状态|Status
运行中|Running
已结束|Ended
取消所选任务|Cancel selected task
已请求取消|Cancellation requested
工程后台任务|Project background task
原像素对比|Original-pixel comparison
加载原像素对比|Load original-pixel comparison
方案原像素对比|Proposal original-pixel comparison
当前调整|Current adjustments
方案效果|Proposed result
通用|General
外观与语言|Appearance and language
性能与存储|Performance and storage
导入|Import
显示与色彩|Display and color
AI 与服务|AI and services
外部编辑|External editor
保存与恢复|Save and recovery
应用默认值|Application defaults
工程覆盖|Project overrides
恢复当前页默认值|Reset this page
应用|Apply
关闭|Close
保存|Save
取消|Cancel
确定|OK
是|Yes
否|No
恢复|Recover
丢弃|Discard
设置已保存|Settings saved
保存中…|Saving…
已保存|Saved
等待保存|Waiting to save
保存失败：|Save failed:
浅色|Light
深色|Dark
跟随系统|Follow system
外观|Appearance
外观：浅色|Appearance: Light
外观：深色|Appearance: Dark
外观：系统|Appearance: System
界面语言|Interface language
中文|Chinese
自动|Automatic
自动加速|Auto
计算设备|Compute device
实际设备|Active device
计算设备与实际路径|Compute device and actual route
专用显存|Dedicated VRAM
专用显存约|Approx. dedicated VRAM
显存约|Approx. VRAM
手动模式|Manual mode
未检测到 GPU|No GPU detected
（待验证）| (pending verification)
按图像大小与操作选择；尚未执行 GPU 滤镜|Selected by image size and operation; GPU filters have not run yet
部分处理仍由 CPU 完成；小图与缓存命中保持 CPU 快速路径。|Some processing still runs on the CPU; small images and cache hits use the fast CPU path.
ONNX 没有可用的 GPU 后端；像素操作使用 CPU，滤镜按成本选择设备。|No GPU provider is available for ONNX; pixel operations use the CPU, and filters select a device by cost.
手动 CPU 模式；滤镜和像素操作使用 CPU。|Manual CPU mode; filters and pixel operations use the CPU.
没有可用的 OpenCL GPU；邻域工具使用 CPU。|No OpenCL GPU is available; neighborhood tools use the CPU.
OpenCL 邻域细节|OpenCL neighborhood detail
手动 CPU 模式|Manual CPU mode
部分处理仍由 CPU 完成。|Some processing still runs on the CPU.
最近计算步骤；部分处理仍由 CPU 完成。|Recent compute stage; some processing still runs on the CPU.
回退原因|Fallback reason
渲染内存预算|Render memory budget
缩略图磁盘预算|Thumbnail disk budget
实时适屏预览上限|Fit preview limit
清理缓存|Clear caches
缓存目录|Cache directory
缓存清理已推迟到任务结束|Cache cleanup deferred until tasks finish
缓存已清理|Caches cleared
复制诊断|Copy diagnostics
打开日志|Open logs
内存缓存|Memory cache
磁盘预览|Disk previews
代理|Proxies
模型|Model
模型设置|Model settings
生成设置|Generation settings
本地模型|Local models
下载本地模型|Download local models
下载|Download
已安装|Installed
未安装|Not installed
用途|Purpose
大小|Size
RAW 默认风格|Default RAW look
其他图片默认风格|Default image look
按相机规则|Camera rules
元数据预设|Metadata preset
实际导入预览|Import preview
关键词|Keywords
版权|Copyright
作者|Creator
无|None
中性|Neutral
鲜艳|Vivid
黑白|Black and white
显示器 ICC|Monitor ICC
自动检测|Automatic detection
渲染意图|Rendering intent
黑点补偿|Black point compensation
相对色度|Relative colorimetric
绝对色度|Absolute colorimetric
感知|Perceptual
饱和度|Saturation
可执行程序|Executable
参数数组|Argument array
交换格式|Interchange format
自动保存|Autosave
保存延迟|Save delay
数据库备份保留数|Database backup retention
备份当前工程|Back up project
恢复会话|Recovery sessions
分享非秘密设置|Share settings without secrets
导入设置|Import settings
工程|Project
保存工程|Save project
保存选片集|Save album
保存全部照片的编辑|Save edits for all photos
保存调整到 Photo Agent|Save edits to Photo Agent
快捷编辑|Quick edit
Photo Agent 新建工程|New Photo Agent project
新建 Agent 工程|New Agent project
打开 Agent 工程|Open Agent project
最近工程|Recent projects
最近的 Photo Agent 工程|Recent Photo Agent projects
暂无最近工程|No recent projects
搜索工程|Search projects
打开|Open
打开结果|Open result
导出|Export
导出 PNG|Export PNG
导入照片|Import photos
导入文件夹|Import folder
重新扫描|Rescan
扫描照片|Scan photos
扫描已完成，请整理当前工程，概述建议。|Scanning finished. Organize this project and summarize suggestions.
导入并扫描照片|Import and scan photos
找图|Find photos
检索照片|Search photos
全部|All
查看全部|Show all
上一页|Previous
下一页|Next
照片编辑|Edit photo
标注人数 / 标签|Annotate people / tags
标注人数|Annotate person count
标注标签|Annotate tags
标签（用逗号分隔）|Tags (comma separated)
画面中的人数（-1 表示未知）|People in the photo (-1 = unknown)
匿名人物簇|Anonymous person clusters
完全重复|Exact duplicates
相似候选|Similar candidates
质量提示|Quality hints
推荐修图|Recommend edits
整理建议|Organization suggestions
编辑方案|Edit proposals
查看并应用方案|Review and apply proposal
预览方案|Preview proposal
清空当前方案|Clear current proposals
派生版本|Derived versions
创建派生版本|Create derived version
执行记录|Execution log
发送|Send
取消等待与排队|Cancel waiting and queued jobs
停止|Stop
你|You
轮|rounds
Agent 正在处理|Agent is working
当前照片|Current photo
找图：去年夏天海边的日落，画面中有两个人|Find photos: a seaside sunset last summer with two people
整理这个相册 / 找图 / 调整选中的照片|Organize this album / find photos / edit selected photos
请选择照片。|Select photos.
请选择一张照片。|Select one photo.
请选择 1–30 张照片。|Select 1–30 photos.
请等待当前任务完成或取消。|Wait for the current task or cancel it.
侧栏|Sidebar
显示侧栏|Show sidebar
撤销|Undo
撤销 (Ctrl+Z)|Undo (Ctrl+Z)
重做|Redo
重做 (Ctrl+Shift+Z)|Redo (Ctrl+Shift+Z)
前后对比|Before / after
原片|Original
按住查看原片|Hold to view original
成片预览|Final preview
尚未打开原片|No photo opened
适应窗口|Fit to window
适应|Fit
调整|Adjustments
调整工具|Editing tools
工具|Tools
直方图|Histogram
显示或收起直方图与溢出提示|Show or hide histogram and clipping warnings
暗部溢出|Shadow clipping
高光溢出|Highlight clipping
曝光与动态范围|Exposure and dynamic range
曝光 EV|Exposure EV
对比度|Contrast
暗部|Shadows
亮部|Highlights
黑色|Blacks
白色|Whites
色温偏移|Temperature shift
色调|Tint
白平衡|White balance
还原相机白平衡|Restore camera white balance
全局色彩|Global color
自然饱和度|Vibrance
质感|Presence
去薄雾|Dehaze
清晰度|Clarity
纹理|Texture
细节|Detail
锐化|Sharpening
降噪|Noise reduction
明度降噪|Luminance noise reduction
彩色杂点|Color noise
明度|Luminance
明度范围|Luminance range
亮度|Brightness
亮度上限|Maximum luminance
亮度下限|Minimum luminance
范围羽化|Range feather
过渡范围|Transition range
色彩|Color
颜色|Color
色彩混合器|Color mixer
黑白处理|Black and white treatment
RGB 与通道曲线|RGB and channel curves
分段直线|Piecewise linear
平滑曲线|Smooth curve
输入|Input
输出|Output
重置当前通道|Reset current channel
重置本组|Reset group
重置|Reset
色相|Hue
色相范围|Hue range
饱和度范围|Saturation range
点颜色|Point color
吸管取样|Eyedropper
在原片上取样颜色|Sample color from original
启用当前样本|Enable current sample
删除颜色样本|Delete color sample
显示范围|Show range
最多支持 16 个颜色样本。|At most 16 color samples are supported.
校准|Calibration
颜色配置|Color profile
配置强度 %|Profile amount %
导入颜色配置|Import color profile
导入 LUT / RGB ICC / DCP…|Import LUT / RGB ICC / DCP…
移除配置文件|Remove profile
未应用配置文件。|No profile applied.
配置|Profile
光学|Optics
几何|Geometry
镜头标定|Lens calibration
导入本地镜头标定|Import lens calibration
导入本地 Lensfun XML…|Import local Lensfun XML…
应用所选镜头校正|Apply selected lens correction
只使用导入文件中的 poly3 / poly5 / PTLens 标定。|Uses only poly3 / poly5 / PTLens calibration from the imported file.
红眼|Red eye
瞳孔|Pupil
在原片上标记瞳孔中心|Mark pupil center on the original
瞳孔半径 / 原片短边|Pupil radius / original short edge
红眼压暗|Red eye darkening
应用强度 %|Amount %
删除当前瞳孔|Delete current pupil
裁切|Crop
裁切：|Crop:
原片比例|Original aspect ratio
自由比例|Free aspect ratio
完整画面|Full frame
清除裁切|Clear crop
重新构图|Recompose
重新裁切|Recrop
确认裁切|Confirm crop
水平校正  °|Straighten °
水平翻转|Flip horizontally
垂直翻转|Flip vertically
顺时针旋转 90°|Rotate 90° clockwise
蒙版|Masks
画笔|Brush
画笔大小|Brush size
橡皮擦|Eraser
渐变|Gradient
径向|Radial
羽化|Feather
不透明度|Opacity
反选|Invert
启用|Enabled
删除蒙版|Delete mask
局部调整|Local adjustments
局部曝光 EV|Local exposure EV
局部亮部|Local highlights
局部暗部|Local shadows
局部色温|Local temperature
局部色调|Local tint
局部饱和度|Local saturation
局部清晰度|Local clarity
局部锐化|Local sharpening
水印|Watermark
模板水印|Template watermark
智能水印|Smart watermark
水印与边框|Watermark and border
水印版式|Watermark layout
边框预设|Border preset
边框宽度|Border width
显示位置|Position
相机品牌|Camera brand
机身标志|Camera body logo
镜头标志|Lens logo
导出时添加水印边框|Add watermark border on export
水印预览|Watermark preview
边框预览|Border preview
打开大图水印预览…|Open large watermark preview…
只有保留画面之外添加边框，照片内容不会被遮挡。|Adds a border outside the photo without covering its content.
只在保留画面之外添加边框，照片内容不会被遮挡。|Adds a border outside the photo without covering its content.
选择有权使用的标志|Select a logo you have permission to use
导入 PNG|Import PNG
上|Top
下|Bottom
左|Left
右|Right
红|Red
橙|Orange
黄|Yellow
绿|Green
青|Aqua
蓝|Blue
紫|Purple
洋红|Magenta
服务|Provider
接口地址|Endpoint URL
API Key|API Key
默认地址|Default URL
获取模型列表|Fetch model list
模型 / 接入点|Model / endpoint ID
测试连接|Test connection
超时|Timeout
思考强度|Reasoning effort
默认（由模型决定）|Default (model decides)
关闭（最快、最省）|Off (fastest)
低|Low
中|Medium
高|High
最高|Maximum
本地模型（不需要 API Key）|Local models (no API key)
云端 API|Cloud API
显示|Show
附带预览图|Attach preview image
语音识别|Speech recognition
自动（中文 / English）|Automatic (Chinese / English)
说完后自动修图|Edit automatically after speaking
服务商水印|Provider watermark
已配置|Configured
未配置|Not configured
说出或输入你想要的效果…|Describe the result you want…
示例指令…|Example instruction…
请输入提示词（最多 12000 字）。|Enter a prompt (maximum 12000 characters).
请输入接口地址，并填写 API Key。|Enter the endpoint URL and API key.
请输入接口地址，并填写模型。|Enter the endpoint URL and model.
请选择参考图。|Select a reference image.
选择参考图|Choose reference image
无参考图|No reference image
当前照片|Current photo
外部图片|External image
参考图|Reference image
发送参考图|Send reference image
选择你要生成的主体|Select the subject to generate
请选择生成主体。|Select a subject to generate.
生成数量|Image count
生成记录|Generation history
生成|Generate
生成为|Generate as
生成队列最多 32 张。|The generation queue holds at most 32 images.
编辑提示|Edit prompt
清空当前模板选择和提示词|Clear current template and prompt
清空|Clear
清除|Clear
选择模板|Choose template
保存模板|Save template
删除模板|Delete template
模板名称|Template name
模板已保存|Template saved
自定义|Custom
自定义提示词|Custom prompt
删除自定义模板|Delete custom template
取消等待与排队|Cancel waiting and queued jobs
重新生成|Generate again
图像信息|Image information
暂无记录|No history
清空记录|Clear history
删除记录|Delete record
从图集中移除|Remove from album
是否从选片集中移除这些图片？|Remove these images from the album?
选片集|Album
选片集 · 0 张|Album · 0 photos
图集列表尚未保存|Album list has unsaved changes
编辑已保存|Edits saved
未保存编辑|Unsaved edits
原图细节已就绪 · 100% 对应原片像素|Original detail ready · 100% matches source pixels
就绪|Ready
待扫描|Pending scan
等待生成|Waiting to generate
排队|Queued
运行|Running
成功|Success
失败|Failed
已取消|Cancelled
没有参数变化|No parameter changes
新照片|New photos
信息|Information
关闭侧栏|Hide sidebar
折叠聊天|Collapse chat
展开聊天|Expand chat
返回工程|Back to project
工程已保存 · 原片未修改|Project saved · Original untouched
没有可恢复的会话|No recoverable sessions
恢复未保存的快捷编辑会话？|Recover the unsaved quick-edit session?
保存失败，请重试或取消关闭。|Saving failed. Retry or cancel closing.
设置未保存：|Settings not saved:
操作未完成|Operation incomplete
正在读取|Reading
正在导出|Exporting
正在更新预览|Updating preview
正在读取原图细节|Reading original detail
正在生成缩略图|Generating thumbnails
空闲|Idle
应用配置|Apply configuration
实时适屏预览大小|Fit preview size
代理策略|Proxy policy
原片优先|Prefer original
代理优先|Prefer proxy
相册排序|Album order
拍摄时间|Capture time
文件名|Filename
评级|Rating
保存当前照片|Save current photo
创建虚拟副本|Create virtual copy
删除虚拟副本|Delete virtual copy
外观：跟随系统|Appearance: System
默认值已恢复；点击应用保存|Defaults restored; click Apply to save
恢复所选会话|Restore selected session
丢弃所选会话|Discard selected session
会话已恢复；原片未修改|Session restored; original preserved
恢复失败：|Recovery failed:
正在取消模型下载…|Cancelling model download…
取消下载|Cancel download
本地模型校验完成|Local model verification complete
工程文件不存在 · |Project file missing ·\x20
未选择模型|No model selected
对话发送指令与工程摘要；照片分析在本地执行。|Chat sends instructions and the project summary; photo analysis runs locally.
AI 去杂色…|AI color denoise…
AI 超分辨率…|AI super resolution…
Compare 对比|Compare
Survey 候选|Survey candidates
上一组建议|Previous suggestions
下一组建议|Next suggestions
专业|Professional
中点|Midpoint
主体|Subject
背景|Background
天空|Sky
近景|Foreground
主体识别显著对象；近景按相对深度估算较近区域。|Subject finds salient objects; Foreground estimates nearby areas from relative depth.
背景为主体的反选。自动蒙版可用画笔修整，结果随工程保存。|Background inverts Subject. Refine automatic masks with the brush; results are saved with the project.
人数|People count
人物|People
人物簇校正|Correct person clusters
仅使用导入文件中的 poly3 / poly5 / PTLens 标定。|Uses poly3 / poly5 / PTLens calibrations from the imported file.
从当前相册移除|Remove from current album
代理操作|Proxy actions
保存勾选元数据|Save selected metadata fields
保存筛选预设|Save filter preset
保存预设|Save preset
保留 P|Pick P
保留建议|Keep suggestion
修复|Heal
修复与仿制|Heal and clone
光圈|Aperture
光影|Tone
创建 16 位离线代理|Create 16-bit offline proxies
创建快照|Create snapshot
删除|Delete
删除选中|Delete selected
勾选字段后批量应用；多个值保持原状，空文字仅在勾选时清空。|Check fields to apply in bulk. Mixed values remain unchanged; blank text clears only checked fields.
去紫边|Remove purple fringe
去绿边|Remove green fringe
另存为|Save as
同步调整|Sync adjustments
启用选中笔划|Enable selected stroke
品牌文字|Brand text
回收站只隐藏工程引用；不会删除原片。虚拟副本与配方快照是不同功能。|Trash hides project references while preserving originals. Virtual copies and recipe snapshots serve different purposes.
在主色彩面板启用黑白后，八色混色生效。|The eight-color mixer applies when Black and white is enabled in the main color panel.
地点|Location
垂直偏移|Vertical offset
垂直透视|Vertical perspective
堆叠操作|Stack actions
备份并清空选中回收记录|Back up and clear selected trash records
导航器|Navigator
平衡 · 暗部 / 高光|Balance · Shadows / Highlights
建立堆叠|Create stack
建立虚拟副本|Create virtual copy
快门|Shutter
忽略建议|Ignore suggestion
恢复选中引用|Restore selected references
拒绝 X|Reject X
提示词|Prompt
撤销本次|Undo this action
撤销管理动作|Undo catalog action
重做管理动作|Redo catalog action
效果|Effects
数据库备份|Database backup
数量|Count
新建智能相册|Create smart album
新建相册|Create album
新建相册文件夹|Create album folder
暗角|Vignette
暗角 · 暗 / 亮|Vignette · Dark / Light
暗角中点|Vignette midpoint
替换目录前缀|Replace folder prefix
本地|Local
机身|Camera body
标记已处理|Mark as processed
标题|Title
检查缺失原片|Check missing originals
检索重关联目录|Search relink folder
水平偏移|Horizontal offset
水平透视|Horizontal perspective
添加 / 减去 / 交集…|Add / Subtract / Intersect…
添加到相册|Add to album
清除取样点|Clear sample points
点选相似颜色区域|Pick an area with similar colors
焦距|Focal length
画笔修整自动蒙版|Refine automatic mask with brush
画笔直径 %|Brush diameter %
畸变|Distortion
相似颜色容差|Similar color tolerance
相册名称 / 父级 / 规则|Album name / Parent / Rules
相册封面|Album cover
相机色温不可用|Camera temperature unavailable
移入工程回收站|Move to project trash
筛选条件|Filter conditions
筛选：全部照片|Filter: All photos
红原色色相|Red primary hue
红原色饱和度|Red primary saturation
红青色差|Red/cyan aberration
纵横变形|Aspect distortion
线性起点|Linear start
绿原色色相|Green primary hue
绿原色饱和度|Green primary saturation
缩放 %|Scale %
胶片颗粒|Film grain
自动前进|Auto advance
自动堆叠|Auto stack
自动选择|Auto select
自定义相册顺序|Custom album order
色彩分级|Color grading
重置色彩分级|Reset color grading
色彩平滑|Color smoothing
色温  K · 无可用基准|Temperature K · No baseline available
蓝原色色相|Blue primary hue
蓝原色饱和度|Blue primary saturation
蓝黄色差|Blue/yellow aberration
语音|Voice
说明|Description
调色|Color editing
选择创意模板|Choose creative template
重关联原片|Relink original
锐化半径 px|Sharpening radius px
锐化遮罩|Sharpening mask
锐化量|Sharpening amount
镜头|Lens
镜头暗角|Lens vignetting
阴影色调|Shadow tint
降噪对比|Denoise comparison
降序|Descending
预设强度|Preset amount
已索引|Indexed
照片|Photos
筛选|Filter
勾选|Selected
所选|Selected
已启用|Enabled
未启用|Disabled
个模型|models
线程|threads
DeepSeek 深度求索|DeepSeek
Seedream 或兼容 /images/generations 的服务。|Seedream or a service compatible with /images/generations.
生成按服务商计费；提示词与勾选的参考图会发送到该接口。|Generation is billed by the provider; prompts and selected reference images are sent to this endpoint.
中间调|Midtones
高光|Highlights
仅外观：色表与曲线|Look only: tables and curves
仿制图章|Clone stamp
修图|Edit
其他本地 OpenAI 兼容服务（vLLM、Jan、LocalAI…）|Other local OpenAI-compatible services (vLLM, Jan, LocalAI…)
兼容显影 · sRGB|Compatible development · sRGB
创意模板生成|Creative template generation
风格化生成|Style generation
回收站|Trash
外部执行前确认费用与上传|Confirm costs and uploads before external execution
启动时提示恢复会话|Offer recovery sessions on startup
宽色域显影 · Rec.2020|Wide gamut development · Rec.2020
导入时间|Import time
层级关键词以 / 分层，多个关键词用逗号分隔|Separate keyword levels with / and multiple keywords with commas
山野绿|Forest green
已编辑|Edited
快照|Snapshots
指令|Instructions
无标签|No label
智谱 GLM|Zhipu GLM
替换|Replace
最近导入|Recently imported
月之暗面 Kimi|Moonshot Kimi
本地服务通常留空|Usually blank for local services
污点修复|Spot healing
火山方舟 · 豆包|Volcengine Ark · Doubao
点“获取模型列表”或直接填写|Click Get models or enter a model name
相册管理|Album management
管理相册|Manage albums
相机参考显影|Camera reference development
相机校色：需要 RAW 原生 RGB|Camera calibration: requires native RAW RGB
硅基流动 SiliconFlow|SiliconFlow
移除|Remove
米色|Beige
线性显影（旧版）|Linear development (legacy)
光影显影|Tone development
显影|Development
编辑时间|Edit time
缺失原片|Missing originals
自动识别|Auto detection
自动：RAW 原生校色／普通照片外观|Auto: native RAW calibration / photo look
自定义 OpenAI 兼容接口|Custom OpenAI-compatible endpoint
追加|Append
选中照片|Selected photos
选择 ICC…|Choose ICC…
选择程序…|Choose program…
阿里云百炼 · 通义千问|Alibaba Cloud Model Studio · Qwen
预设|Presets
内存缓存|Memory cache
模型文件|Model files
工程预览|Project previews
独立代理|Independent proxies
人脸检测|Face detection
匿名人物聚类|Anonymous person clustering
语义检索|Semantic search
场景分析|Scene analysis
文本分词|Text tokenization
自动黑白混色|Auto black and white mixer
在照片上拖动调整黑白色带|Drag on the photo to adjust black and white color bands
显示颜色范围|Show color range
约束裁切到有效图像|Constrain crop to valid image area
显示几何参考网格|Show geometry reference grid
颜色样本名称|Color sample name
代理最长边|Proxy longest edge
收起对话|Collapse chat
恢复默认布局|Restore default layout
布局未保存：|Layout not saved:
"""
CATALOG = dict(line.split('|', 1) for line in _PAIRS.splitlines() if '|' in line)
CATALOG.update(
    {
        '当前设备：': 'Current device: ',
        '当前路径：': 'Current route: ',
        '最近计算步骤：': 'Recent compute stage: ',
        '驱动：': 'Driver: ',
        'ONNX 后端：': 'ONNX providers: ',
        '滤镜设备：': 'Filter device: ',
        'GPU 步骤：': 'GPU stages: ',
        '滤镜：': 'Filters: ',
        'OpenCL 检测失败：': 'OpenCL detection failed: ',
        'OpenCL 邻域工具失败，已回退 CPU：': 'OpenCL neighborhood tools failed; CPU fallback: ',
        '全部照片': 'All photos',
        '导出时启用水印': 'Enable watermark on export',
        '品牌、标志与拍摄参数': 'Brand, logos and capture parameters',
        '拍摄参数覆盖': 'Capture parameter overrides',
        '参数名称': 'Parameter name',
        '覆盖值': 'Override value',
        '处理步骤与嵌套文字': 'Processing steps and nested text',
        '阴影半径': 'Shadow radius',
        '阴影颜色': 'Shadow color',
        '圆角半径': 'Corner radius',
        '模糊半径': 'Blur radius',
        '左右对齐': 'Horizontal alignment',
        '上下对齐': 'Vertical alignment',
        '文字片段': 'Text segments',
        '文字来源': 'Text source',
        '内容来源': 'Content source',
        'px / 数值': 'px / value',
        '{{vh(4)}} 或其他模板表达式': '{{vh(4)}} or another template expression',
        '图片 / 标志': 'Image / logo',
        '圆角': 'Rounded corners',
        '居中标志': 'Center logo',
        '恢复当前版式的默认配置': 'Restore defaults for this layout',
        '文字': 'Text',
        '旧版标准 / 自定义水印': 'Standard / custom layout',
        '旧版阴影': 'Shadow layout',
        '时间戳水印': 'Timestamp watermark',
        '模糊': 'Blur',
        '添加': 'Add',
        '缩放': 'Resize',
        '背景模糊': 'Background blur',
        '自动读取照片参数；只添加需要替换的值。可编辑任意 EXIF 名称，文字模板可引用这些参数。': 'Photo metadata is read automatically. Add only values to override; text templates can reference any EXIF field.',
        '表达式': 'Expression',
        '裁剪': 'Crop',
        '边距': 'Margins',
        '选择树中的步骤、文字位置或片段编辑。尺寸支持 px、vh、vw 和表达式；vh / vw 基于原始照片。输入序号 0 为照片，1 为第一个步骤输出。': 'Select a step, text position or segment in the tree. Dimensions support px, vh, vw and expressions. Input index 0 is the photo; index 1 is the first step output.',
        '阴影': 'Shadow',
        '＋ 步骤': 'Add step',
        '＋ 添加数值': 'Add value',
        '＋ 添加资源': 'Add resource',
        '边距颜色': 'Margin color',
        '填充比例 W:H': 'Padding ratio W:H',
        '中间间距': 'Middle spacing',
        '右侧对齐': 'Right alignment',
        '居中标志高度': 'Center logo height',
        '左标志资源': 'Left logo resource',
        '右标志资源': 'Right logo resource',
        '居中标志资源': 'Center logo resource',
        '裁剪偏移': 'Crop offset',
        '起始颜色': 'Start color',
        '结束颜色': 'End color',
        '渐变方式': 'Gradient interpolation',
        '水平对齐': 'Horizontal alignment',
        '垂直对齐': 'Vertical alignment',
        '文字对齐': 'Text alignment',
        '背景颜色': 'Background color',
        '叠放坐标': 'Layer offsets',
        '叠放顺序权重': 'Layer order weights',
        '输入图像序号': 'Input image indexes',
        '输入图像资源': 'Input image resources',
        '图片资源': 'Image resource',
        '普通字大小等级': 'Regular text size level',
        '粗体字大小等级': 'Bold text size level',
        '粗体字体': 'Bold font',
        '标志位置': 'Logo position',
        '启用标志': 'Enable logo',
        '标志资源': 'Logo resource',
        '背景留白比例': 'Background padding ratio',
        '背景白色混合': 'Background white blend',
        '白框宽度': 'White frame width',
        '机身文字颜色': 'Camera text color',
        '参数文字颜色': 'Capture parameter color',
        '修剪左侧': 'Trim left',
        '修剪右侧': 'Trim right',
        '修剪上侧': 'Trim top',
        '修剪下侧': 'Trim bottom',
        '在结构树中选择一个处理步骤或文字片段。': 'Select a processing step or text segment in the tree.',
        '预览使用最终裁剪、旋转后的画面与导出尺寸计算水印。': 'Preview uses the final crop, rotation and export dimensions.',
        '打开照片后显示水印预览': 'Open a photo to preview the watermark',
        '文字 / 模板': 'Text / template',
        '字体': 'Font',
        '粗体': 'Bold',
        '高度': 'Height',
        '宽度': 'Width',
        '左边距': 'Left margin',
        '右边距': 'Right margin',
        '上边距': 'Top margin',
        '下边距': 'Bottom margin',
        '分隔线颜色': 'Divider color',
        '分隔线宽度': 'Divider width',
        '文字间距': 'Text spacing',
        '中央标志高度': 'Center logo height',
        '左上文字': 'Upper left text',
        '左下文字': 'Lower left text',
        '右上文字': 'Upper right text',
        '右下文字': 'Lower right text',
        '左标志': 'Left logo',
        '右标志': 'Right logo',
        '中央标志': 'Center logo',
        '分段文字': 'Segmented text',
        '纯色画布': 'Solid color canvas',
        '渐变画布': 'Gradient canvas',
        '比例填充': 'Aspect ratio padding',
        '修剪空白': 'Trim empty space',
        '修剪文字空白': 'Trim text space',
        '步骤类型': 'Step type',
        '输入索引': 'Input indexes',
        '图层顺序': 'Layer order',
        '位置偏移': 'Position offset',
        '叠放对齐': 'Layer alignment',
        '拼接': 'Concatenate',
        '向上移动数值': 'Move value up',
        '向下移动数值': 'Move value down',
        '删除数值': 'Remove value',
        '上移选中步骤或片段': 'Move selected step or segment up',
        '下移选中步骤或片段': 'Move selected step or segment down',
        '编辑：': 'Editing: ',
        '使用 35mm 等效焦距': 'Use 35mm equivalent focal length',
        '已导入 PNG': 'PNG imported',
        '模板 / 自动': 'Template / automatic',
        '标准水印2': 'standard2',
        '标准水印': 'standard1',
        '尼康专用背景模糊': 'nikon_blur',
        '右下角参数': 'normal1',
        '文件夹名 + 时间': 'normal2',
        'Logo 居中': 'center_logo',
        '旧版 normal · Logo 居左': 'normal / left logo',
        '旧版 normal · Logo 居右': 'normal / right logo',
        '旧版黑红 · Logo 居左': 'Black and red / left logo',
        '旧版黑红 · Logo 居右': 'Black and red / right logo',
        '旧版自定义配置': 'Custom layout',
        '旧版 1:1 填充': 'Square padding',
        '旧版简洁': 'Simple layout',
        '旧版背景模糊': 'Blurred background',
        '旧版背景模糊 + 白框': 'Blurred background and white frame',
        '旧版白色边框': 'White frame',
        '重置选项': 'Reset options',
        '重置当前分组或整张照片': 'Reset current section or the whole photo',
        '选择一张有原片或可用离线代理的照片。': 'Select one photo with an original or a usable offline proxy.',
        '选择一张照片标注人数与标签。': 'Select one photo to annotate people and tags.',
        '选择 1–30 张已扫描且原片可用的照片。': 'Select 1–30 scanned photos with available originals.',
        '选择一条整理建议后记录决定。': 'Select an organization suggestion before recording a decision.',
        '重置所有调整': 'Reset all adjustments',
        '设置范围': 'Settings scope',
        '设置目录': 'Settings directory',
        '工作区': 'Workspace',
        '常规': 'General',
        '工程偏好': 'Project preferences',
        '照片与色彩': 'Photos and color',
        '计算': 'Compute',
        '预览与缓存': 'Previews and cache',
        '维护与诊断': 'Maintenance and diagnostics',
        '对话与修图': 'Chat and editing',
        '图像生成': 'Image generation',
        '语音': 'Voice',
        '工作流': 'Workflow',
        '工程备份': 'Project backups',
        '会话恢复': 'Session recovery',
        '工作区布局': 'Workspace layout',
        '存储用量': 'Storage usage',
        '诊断与任务': 'Diagnostics and tasks',
        '相机规则': 'Camera rules',
        '默认元数据': 'Default metadata',
        '启动与确认': 'Startup and confirmation',
        '界面偏好': 'Interface preferences',
        '浏览与代理': 'Browsing and proxies',
        '默认风格': 'Default looks',
        '色彩管理': 'Color management',
        '计算资源': 'Compute resources',
        '预览预算': 'Preview budgets',
        '缓存维护': 'Cache maintenance',
        '模型服务': 'Model service',
        '生成服务': 'Generation service',
        '语音输入': 'Voice input',
        '模型安装': 'Model installation',
        '交换设置': 'Interchange settings',
        '自动保存策略': 'Autosave policy',
        '备份策略': 'Backup policy',
        '可恢复会话': 'Recoverable sessions',
        '应用默认值用于所有工作区；工程可单独覆盖导入与工程偏好。': 'Application defaults apply to all workspaces. Projects can override import and project preferences.',
        '仅覆盖当前工程的导入与工程偏好；其他设置使用应用默认值。': 'Only import and project preferences are overridden for this project. Other settings use application defaults.',
        '构图': 'Composition',
        '局部': 'Local',
        '全局色彩与混合器': 'Global color and mixer',
        '质感与降噪': 'Texture and denoise',
        '裁切与旋转': 'Crop and rotation',
        '蒙版与局部调整': 'Masks and local adjustments',
        '暗角与胶片颗粒': 'Vignette and grain',
        '镜头与光学校正': 'Lens and optical correction',
        '透视与几何': 'Perspective and geometry',
        '相机色彩校准': 'Camera color calibration',
        '精细锐化与降噪': 'Fine sharpening and denoise',
        '黑白混色': 'Black-and-white mixer',
        '点颜色与范围': 'Point color and range',
        '颜色配置文件': 'Color profile',
        '红眼修复': 'Red-eye correction',
        '显影空间': 'Working color space',
        '重置当前分组': 'Reset group',
        '重置：': 'Reset: ',
        '展开一个分组后重置': 'Expand a section to reset it',
        '显影空间决定后续色彩运算的工作空间。': 'Working color space controls the space used for subsequent color operations.',
        '图库导航': 'Library navigation',
        '图库管理': 'Library management',
        '选片': 'Culling',
        '标记': 'Flag',
        '选择照片后可编辑或整理': 'Select photos to edit or organize',
        '选择照片以编辑或整理': 'Select photos to edit or organize',
        '保留 / 拒绝': 'Pick / reject',
        '更多操作': 'More actions',
        '相册与筛选': 'Albums and filters',
        '原片、代理与回收站': 'Originals, proxies and trash',
        '历史与备份': 'History and backups',
        '相册与文件夹': 'Albums and folders',
        '常用筛选': 'Saved filters',
        '常用筛选预设': 'Saved filter presets',
        '内置筛选': 'Built-in filters',
        '选中照片': 'Selected photos',
        '添加到相册': 'Add to album',
        '代理尺寸设置': 'Proxy dimensions',
        '离线代理尺寸': 'Offline proxy dimensions',
        '离线代理尺寸…': 'Offline proxy dimensions…',
        '代理最长边；原片保持原始尺寸。': 'Proxy long edge; original dimensions are preserved.',
        '选择需要同步的参数组': 'Choose parameter groups to synchronize',
        '恢复默认选择': 'Restore default selection',
        '选择全部': 'Select all',
        '清空选择': 'Clear selection',
        '勾选或取消本组全部参数': 'Select or clear all parameters in this group',
        '默认保留每张照片的白平衡、裁切和局部调整。': "Defaults preserve each photo's white balance, crop and local adjustments.",
        '颜色与精度': 'Color and precision',
        '尺寸与分辨率': 'Dimensions and resolution',
        '细节与命名': 'Detail and naming',
        '元数据策略': 'Metadata policy',
        '保留信息': 'Retained information',
        '组件外观': 'Component appearance',
        '位置与形状': 'Position and shape',
        '添加组件': 'Add component',
        '选择已有蒙版和运算方式，添加第一个组件。': 'Choose an existing mask and an operation to add the first component.',
    }
)
_LANGUAGE = 'zh'
_MISSING = set()
_NATIVE_ZH = {
    'Save': '保存',
    'Cancel': '取消',
    'OK': '确定',
    'Close': '关闭',
    'Yes': '是',
    'No': '否',
    'Discard': '丢弃',
    'Apply': '应用',
    'Reset': '重置',
    'Open': '打开',
    'Retry': '重试',
    'Abort': '终止',
    'Ignore': '忽略',
    'Restore Defaults': '恢复默认值',
}


def language():
    return _LANGUAGE


def translate(text, lang=None):
    lang = lang or _LANGUAGE
    text = str(text)
    if lang == 'zh':
        return text
    if text in CATALOG:
        return CATALOG[text]
    patterns = [
        (r'^已选择 (\d+) 张$', r'\1 photos selected'),
        (r'^选片集 · (\d+) 张 / 已选 (\d+) 张$', r'Album · \1 photos / \2 selected'),
        (r'^选片集 · (\d+) 张$', r'Album · \1 photos'),
        (r'^Agent · 第 (\d+) 轮$', r'Agent · Round \1'),
        (r'^保存失败：(.*)$', r'Save failed:\1'),
        (r'^工程文件不存在 · (.*)$', r'Project file missing · \1'),
        (r'^已保存：(.*)$', r'Saved:\1'),
        (r'^已导入 (\d+) 张(.*)$', r'Imported \1 photos\2'),
    ]
    for pattern, replacement in patterns:
        if re.match(pattern, text, flags=re.S):
            return re.sub(pattern, replacement, text, flags=re.S)
    # Short suffix/prefix messages retain interpolated counts, paths and model IDs.
    output = text
    for source in sorted(CATALOG, key=len, reverse=True):
        if len(source) > 1 and source in output:
            output = output.replace(source, CATALOG[source])
    output = output.replace(' 张', ' photos').replace(' 个', ' items').replace(' 秒', ' seconds')
    if re.search('[\u4e00-\u9fff]', output):
        _MISSING.add(text)
    return output


def untranslated_messages():
    return sorted(_MISSING)


def register_messages(messages):
    """Feature modules may extend the UI catalog while keeping recipe IDs stable."""
    if not isinstance(messages, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in messages.items()
    ):
        raise ValueError('Message catalog requires string pairs')
    CATALOG.update(messages)


def set_message(widget, text):
    """Update a dynamic UI message without losing its source on language changes."""
    text = str(text)
    rendered = translate(text)
    widget.setProperty('_i18n_source_text', text)
    widget.setProperty('_i18n_last_text', rendered)
    widget.setText(rendered)


class ApplicationTranslator(QTranslator):
    def translate(self, context, sourceText, disambiguation=None, n=-1):
        bare = sourceText.replace('&', '')
        if _LANGUAGE == 'zh' and bare in _NATIVE_ZH:
            return _NATIVE_ZH[bare]
        return ''


def set_language(lang):
    global _LANGUAGE
    if lang not in ('zh', 'en'):
        raise ValueError('Unsupported application language')
    _LANGUAGE = lang
    app = QApplication.instance()
    if app is not None:
        if not hasattr(app, '_ccraw_translator'):
            app._ccraw_translator = ApplicationTranslator(app)
            app.installTranslator(app._ccraw_translator)
        for window in app.topLevelWidgets():
            localize_window(window, lang)


def _replace(widget, name, getter, setter, lang):
    current = str(getter())
    key, last_key = '_i18n_source_' + name, '_i18n_last_' + name
    source, last = widget.property(key), widget.property(last_key)
    if source is None or current != last:
        source = current
        widget.setProperty(key, source)
    rendered = translate(source, lang)
    if current != rendered:
        blocked = widget.blockSignals(True)
        setter(rendered)
        widget.blockSignals(blocked)
    widget.setProperty(last_key, rendered)


def source_item(combo, index=None):
    index = combo.currentIndex() if index is None else index
    source = combo.property(f'_i18n_source_item{index}')
    return source if source is not None else combo.itemText(index)


def _localize_tree(tree, lang):
    blocked = tree.blockSignals(True)
    iterator = QTreeWidgetItemIterator(tree)
    while iterator.value() is not None:
        item = iterator.value()
        for column in range(tree.columnCount()):
            if item.data(column, Qt.ItemDataRole.UserRole + 31):
                continue
            current = item.text(column)
            source = item.data(column, Qt.ItemDataRole.UserRole + 30)
            last = item.data(column, Qt.ItemDataRole.UserRole + 29)
            if source is None or current != last:
                source = current
                item.setData(column, Qt.ItemDataRole.UserRole + 30, source)
            rendered = translate(source, lang)
            item.setText(column, rendered)
            item.setData(column, Qt.ItemDataRole.UserRole + 29, rendered)
        iterator += 1
    tree.blockSignals(blocked)


def localize_window(window, lang=None):
    lang = lang or _LANGUAGE
    widgets = [window] + window.findChildren(QWidget)
    for widget in widgets:
        if isinstance(widget, QGroupBox) and not widget.property('user_content'):
            _replace(widget, 'group_title', widget.title, widget.setTitle, lang)
        if isinstance(widget, QTreeWidget) and not widget.property('user_content'):
            _localize_tree(widget, lang)
        if isinstance(widget, (QAbstractButton, QLabel)) and not widget.property('user_content'):
            _replace(widget, 'text', widget.text, widget.setText, lang)
        if isinstance(widget, (QLineEdit, QPlainTextEdit)):
            _replace(widget, 'placeholder', widget.placeholderText, widget.setPlaceholderText, lang)
        if (
            isinstance(widget, QComboBox)
            and not widget.isEditable()
            and not widget.property('user_content')
        ):
            for index in range(widget.count()):
                _replace(
                    widget,
                    f'item{index}',
                    lambda i=index: widget.itemText(i),
                    lambda text, i=index: widget.setItemText(i, text),
                    lang,
                )
        if isinstance(widget, (QTabWidget, QTabBar)):
            for index in range(widget.count()):
                _replace(
                    widget,
                    f'tab{index}',
                    lambda i=index: widget.tabText(i),
                    lambda text, i=index: widget.setTabText(i, text),
                    lang,
                )
        for name, getter, setter in (
            ('tooltip', widget.toolTip, widget.setToolTip),
            ('accessible', widget.accessibleName, widget.setAccessibleName),
        ):
            if getter():
                _replace(widget, name, getter, setter, lang)
        if widget.isWindow() and widget.windowTitle() and not widget.property('user_content_title'):
            _replace(widget, 'title', widget.windowTitle, widget.setWindowTitle, lang)
        if isinstance(widget, QDialogButtonBox):
            for standard, source in (
                (QDialogButtonBox.StandardButton.Save, '保存'),
                (QDialogButtonBox.StandardButton.Cancel, '取消'),
                (QDialogButtonBox.StandardButton.Close, '关闭'),
                (QDialogButtonBox.StandardButton.Ok, '确定'),
                (QDialogButtonBox.StandardButton.Apply, '应用'),
                (QDialogButtonBox.StandardButton.RestoreDefaults, '恢复当前页默认值'),
            ):
                button = widget.button(standard)
                if button:
                    button.setText(translate(source, lang))
    for action in window.findChildren(QAction):
        _replace(action, 'text', action.text, action.setText, lang)
        if action.toolTip():
            _replace(action, 'tooltip', action.toolTip, action.setToolTip, lang)
