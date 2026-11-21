# 第三方资源与权利边界

本仓库的 **MIT 许可证只覆盖项目作者拥有权利的原创代码、测试与原创文档**。
下列第三方材料**不因为出现在本仓库或安装包中而自动转为 MIT**，其权利仍归各自权利人。

> 这份文件说明来源与边界，**不是授权**。任何一项再分发依据未确认的内容，都在最后单列为发布风险。

## 资源清单

| 资源 | 来源 / 生成方式 | 再分发依据 | 结论 |
|---|---|---|---|
| `mihome_pc/static/logo.png`、`favicon.png` | 本项目自制素材（仓库内无引用外部素材的生成脚本，也无第三方来源记录） | 项目自有 | MIT |
| `mihome_pc/assets/mihome.ico` | 由 `logo.png` 经 `tools/make_icon.py` 生成 | 同 logo | MIT |
| `mihome_pc/assets/dynamic_ranges.json` | 本项目实测：`tools/probe_fryer_limits.py` 对自有空气炸锅逐档写入后回读得到的跨属性范围 | 项目自有（测量数据） | MIT |
| `mihome_pc/style.css`、`app.js`、`index.html` | 本项目手写（无框架、无第三方库） | 项目自有 | MIT |
| `tests/fixtures/specs/*.json`、`mihome_pc/assets/demo/*.json` | 小米公开的 MIoT spec 实例（`miot-spec.org` 公开页面下载），仅作解析夹具与演示数据 | **未确认**：按公开规范数据使用，不声称 MIT | 见风险 2 |
| `mihome_pc/assets/catalog.json`、`catalog_categories.json` | `tools/make_catalog.py` 从**米家 APK 内置** `assets/device_config/plugin_config_cn-zh_cn` 提取（12,160 条型号元数据） | **未确认**：数据源自小米客户端，公开再分发依据不成立 | ⚠️ **见风险 1** |
| `mihome_pc/assets/card_layouts.json` | `tools/make_card_layouts.py` 从**米家 APK 内置** `assets/cardControl/configDes.json`（缓存的云端响应）提取 | **未确认**：同上 | ⚠️ **见风险 1** |
| 插件文案 / 故障码解释 | **运行时不随包分发**：用户登录后由本程序按型号从云端下载、解包 `resources.arsc`，只写入本地 `data/plugin-texts/` | 不涉及再分发 | — |
| 场景图标 | **不随包分发**：界面直接使用云端返回的图标 URL | 不涉及再分发 | — |
| 米家 / Mi Home / 小米 名称与商标 | 仅用于**描述兼容对象** | 商标权归小米 | 见声明 |

## 发布风险（公开前需要处理）

### 风险 1：`catalog.json` / `card_layouts.json` 源自米家 APK（**阻断项**）

这两个文件合计约 2.7 MB，内容是米家客户端内置的型号目录与卡片布局配置，**不是**本项目原创，
也没有获得再分发许可。代码对它们的缺失是**优雅降级**的（`domain/model.py::_load()` 找不到文件就返回空字典）：

* 缺 `catalog.json`：设备列表仍可用，只是没有中文展示名/品类，回退显示型号 urn；
* 缺 `card_layouts.json`：卡片使用通用布局，不使用逐型号的卡片配置。

**建议（二选一，由所有者决定）**：

1. **不把这两个文件放进公开仓库**，改为在 `.gitignore` 中排除，并在 README 说明
   「如需完整型号目录，用你自己拥有的米家安装包运行 `tools/make_catalog.py` / `tools/make_card_layouts.py` 本地生成」；
   安装包（Release 附件）是否附带，也需所有者自行判断。
2. 保留在仓库，但明确标注为「来源不明、再分发依据未确认」——**本文件不建议这样做**，
   因为 NOTICE 不能替代授权。

### 风险 2：MIoT spec 夹具

`tests/fixtures/specs/` 与 `mihome_pc/assets/demo/` 里的 spec JSON 来自小米公开的 MIoT 规范页面，
用于离线测试与演示模式。它们的作用是「让程序能在不联网时跑起来」，不构成对规范的再授权。
如果权利人提出异议，替换为自造的等价结构体即可（解析器只依赖字段形状）。

## 与小米的关系

本项目是**社区开发的非官方桌面客户端**，与小米公司不存在隶属、授权或背书关系。
“米家”“Mi Home”“小米”及相关标识、名称归其权利人所有。本项目通过逆向分析与自有账户验证协议细节，
不包含小米的官方客户端二进制、完整插件包或反编译源码。
