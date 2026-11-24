# 第三方资源与权利边界

本仓库的许可证只覆盖项目作者拥有权利的原创代码、测试、原创文档与原创图标。
自 **0.7.38** 起，这些项目自有内容采用 **PolyForm Noncommercial 1.0.0**；**0.7.37 及更早版本**仍按当时发布的 MIT 条款提供，旧许可文本保存在 `LICENSES/MIT.txt`，不追溯撤销。
下列第三方材料**不因为出现在本仓库或安装包中而自动转为项目许可证**，其权利仍归各自权利人。

> 这份文件说明来源与边界，**不是授权**。任何一项再分发依据未确认的内容，都在最后单列为发布风险。

## 资源清单

| 资源 | 来源 / 生成方式 | 再分发依据 | 结论 |
|---|---|---|---|
| `mihome_pc/static/logo.svg`、`logo.png`、`favicon.png` | 本项目原创“显示器＋控制节点”素材；`logo.svg` 为矢量源文件，位图由 `tools/make_logo.py` 生成 | 项目自有 | 0.7.38 起 PolyForm Noncommercial |
| `mihome_pc/assets/mihome.ico` | 由原创 `logo.png` 经 `tools/make_logo.py` 生成 | 同 logo | 同项目许可证 |
| `mihome_pc/assets/dynamic_ranges.json` | 本项目实测：`tools/probe_fryer_limits.py` 对自有空气炸锅逐档写入后回读得到的跨属性范围 | 项目自有（测量数据） | 同项目许可证 |
| `mihome_pc/static/style.css`、`app.js`、`index.html` | 本项目手写（无框架、无第三方库） | 项目自有 | 同项目许可证 |
| Windows `python38/LICENSE.txt` | 随内嵌 CPython 3.8.10 原样分发的 PSF 及其他组件许可文本 | 各组件原许可证 | 不改变、不覆盖 |
| `tests/fixtures/specs/*.json`、`mihome_pc/assets/demo/*.json` | 小米公开的 MIoT spec 实例（`miot-spec.org` 公开页面下载），仅作解析夹具与演示数据 | **未确认**：按公开规范数据使用，不声称适用项目许可证 | 见风险 2 |
| `mihome_pc/assets/catalog.json`、`catalog_categories.json` | `tools/make_catalog.py` 从**米家 APK 内置** `assets/device_config/plugin_config_cn-zh_cn` 提取（12,160 条型号元数据） | **未确认**：数据源自小米客户端，公开再分发依据不成立 | **不进入任何公开产物；见处理结论 1** |
| `mihome_pc/assets/card_layouts.json` | `tools/make_card_layouts.py` 从**米家 APK 内置** `assets/cardControl/configDes.json`（缓存的云端响应）提取 | **未确认**：同上 | **不进入任何公开产物；见处理结论 1** |
| 插件文案 / 故障码解释 | **运行时不随包分发**：用户登录后由本程序按型号从云端下载、解包 `resources.arsc`，只写入本地 `data/plugin-texts/` | 不涉及再分发 | — |
| 场景图标 | **不随包分发**：界面直接使用云端返回的图标 URL | 不涉及再分发 | — |
| 米家 / Mi Home / 小米 名称与商标 | 仅用于**描述兼容对象** | 商标权归小米 | 见声明 |

## 发布风险与处理状态

### 处理结论 1：`catalog.json` / `catalog_categories.json` / `card_layouts.json` 源自米家 APK（**已阻断公开分发**）

这些文件合计约 2.7 MB，内容是米家客户端内置的型号目录与卡片布局配置，**不是**本项目原创，
也没有获得再分发许可。项目已决定并执行：**三个文件均不进入公开源码仓库、源码压缩包或任何安装包**，
并通过 `.gitignore` 排除。NOTICE 不能替代授权，因此不采用“保留文件但附加来源声明”的方式。

代码对这些文件的缺失会**优雅降级**（`domain/model.py::_load()` 找不到文件就返回空字典）：

* 缺 `catalog.json` / `catalog_categories.json`：设备列表仍可用，只是没有中文展示名/品类，回退显示型号 urn；
* 缺 `card_layouts.json`：卡片使用通用布局，不使用逐型号的卡片配置。

如需完整型号目录或专属卡片布局，用户可使用**自己合法拥有的米家安装包**在本地运行
`tools/make_catalog.py` 和 `tools/make_card_layouts.py` 生成。生成结果只留在本地，不应提交或随公开产物再分发。

### 风险 2：MIoT spec 夹具

`tests/fixtures/specs/` 与 `mihome_pc/assets/demo/` 里的 spec JSON 来自小米公开的 MIoT 规范页面，
用于离线测试与演示模式。它们的作用是「让程序能在不联网时跑起来」，不构成对规范的再授权。
如果权利人提出异议，替换为自造的等价结构体即可（解析器只依赖字段形状）。

## 与小米的关系

本项目是**社区开发的非官方桌面客户端**，与小米公司不存在隶属、授权或背书关系。
“米家”“Mi Home”“小米”及相关标识、名称归其权利人所有。本项目通过逆向分析与自有账户验证协议细节，
不包含小米的官方客户端二进制、完整插件包或反编译源码。
