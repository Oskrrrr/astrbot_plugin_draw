# 画图（astrbot_plugin_draw）

在 AstrBot 里提供 `/画图`、`/生图` 指令和 LLM 画图工具，支持多提供方、模型回退、默认负面词、去水印与最近生成图记录。

## 功能

- `/画图 <描述>` / `/生图 <描述>`：管理员指令，生成后直接发图。
- LLM 工具 `image_draw`：模型在聊天里自己调用画图，再把图片发给用户。
- 多提供方：一个 `providers.json` 里可以放多个提供方，每个提供方多个模型。
- 模型回退：首选模型失败时可自动换下一个模型（页面可开关）。
- 默认负面词、画风、尺寸、超时、提示词长度都可配置。
- 去水印：给 SiliconFlow 等兼容接口加 `X-Enable-Watermark: 0`。
- 最近生成图：页面显示最近作品，历史记录落 `history.json`。

## 安装

1. 把目录放到 `AstrBot/data/plugins/astrbot_plugin_draw`。
2. 安装依赖：`pip install -r requirements.txt`（主要是 Pillow）。
3. 重启 AstrBot，或重载插件。
4. 打开 WebUI 的「插件 → 画图」页面，配置提供方和模型。

## 配置

| 配置项 | 说明 |
|---|---|
| `providers_file` | 提供方 JSON 路径；默认在插件数据目录下 |
| `model` | 当前使用模型；留空用 `providers.json` 的 `default_model_id` |
| `negative_prompt` | 默认负面词；留空用内置 |
| `style` | 聊天画图的画风；说说配图由说说外挂/角色卡接管 |
| `size` | 图片尺寸；留空用模型默认尺寸 |
| `watermark_off` | 请求去掉 SiliconFlow 水印 |
| `timeout` | 生图超时（秒） |
| `max_prompt_chars` | 提示词最大长度 |

API Key 只存在 `providers.json` 里，页面只回显掩码；日志不会打印完整 key。

## 指令

```text
/画图 一只橘猫在窗台，午后阳光，水彩质感
/生图 同样的描述
```

需要管理员权限；别名 `/生图` 与 `/画图` 等价。

## 与 angel_brush 的关系

`providers.json` 的字段格式兼容 [angel_brush](https://github.com/kawayiYokami/astrbot_plugin_angel_brush)，方便沿用已有的提供方与密钥；实现代码是独立编写的，不修改 angel_brush 的文件。感谢原作者 kawayiYokami 的格式设计。

## 许可

MIT，见 [LICENSE](./LICENSE)。
