<div align="center">

# 🖼️ 说说画师

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-27a7d8)](https://github.com/AstrBotDevs/AstrBot)
[![Release](https://img.shields.io/github/v/release/Oskrrrr/astrbot_plugin_draw?color=27a7d8&label=Release)](https://github.com/Oskrrrr/astrbot_plugin_draw/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)

**让每一条说说都有配图，也支持在聊天里直接 `/画图`。**

</div>

## ✨ 特色

| 你想做什么 | 说说画师怎么帮你 |
|---|---|
| 给说说配图 | 被 [QQ空间说说管家](https://github.com/Oskrrrr/astrbot_plugin_qzone_ext) 调用时，按管家/角色卡给的提示词出图；失败时管家自动退回纯文字 |
| 聊天里直接出图 | `/画图 一只橘猫在窗台` / `/生图 ...`，管理员指令，生成后直接发图 |
| 让模型自己画 | 注册 LLM 工具 `image_draw`，模型可在对话中调用，再把图片发给用户 |
| 多服务商、多模型 | 一个 `providers.json` 放多个提供方，每个提供方多个模型，页面里切换 |
| 首选失败不中断 | 打开「失败自动换模型」，主模型挂了自动试下一个 |
| 控制画面 | 默认负面词、画风、尺寸、超时、提示词长度都可配；支持去 [SiliconFlow](https://siliconflow.cn/) 水印 |
| 看最近作品 | 页面显示最近生成图和提示词，历史落 `history.json` |

## 🚀 快速开始

1. 把目录放到 `AstrBot/data/plugins/astrbot_plugin_draw`。
2. 安装依赖：`pip install -r requirements.txt`（主要是 Pillow）。
3. 重启 AstrBot，或重载插件。
4. 打开 WebUI 的「插件 → 说说画师」页面，配置提供方和模型。

## 🎨 怎么用

```text
/画图 一只橘猫在窗台，午后阳光，水彩质感
/生图 同样的描述
```

需要管理员权限；`/生图` 与 `/画图` 等价。

模型侧也可以直接调用 `image_draw` 工具，工具会返回图片的绝对路径，并提示模型用 `send_message_to_user` 把图片发给用户。

## 🖼️ 给说说配图

[QQ空间说说管家](https://github.com/Oskrrrr/astrbot_plugin_qzone_ext) 发说说时会调用本插件的 `generate()`：

- 说说管家/角色卡决定画面要求、画风、负面词；
- 说说画师只负责用 `providers.json` 里的模型把提示词变成图片；
- 生成失败时，说说管家自动改发纯文字，不会让整条说说失败。

也就是说：**说说画师是“画师”，说说管家是“编辑”**——编辑决定画什么，画师负责画出来。

## ⚙️ 配置

| 配置项 | 说明 |
|---|---|
| `providers_file` | 提供方 JSON 路径；默认在插件数据目录下 |
| `model` | 当前使用模型；留空用 `providers.json` 的 `default_model_id` |
| `negative_prompt` | 默认负面词；留空用内置 |
| `style` | 聊天画图的画风；说说配图由 [QQ空间说说管家](https://github.com/Oskrrrr/astrbot_plugin_qzone_ext)/[角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 接管 |
| `size` | 图片尺寸；留空用模型默认尺寸 |
| `watermark_off` | 请求去掉 [SiliconFlow](https://siliconflow.cn/) 水印 |
| `timeout` | 生图超时（秒） |
| `max_prompt_chars` | 提示词最大长度 |

API Key 只存在 `providers.json` 里，页面只回显掩码；日志不会打印完整 key。

## 🔌 providers.json

`providers.json` 里可以放多个提供方；每个提供方有 `base_url`、`api_keys`、`models`，模型有 `model`、`default_size`、`enabled`。页面上的「新增提供方」「测试连接」「拉取模型」会直接改这个文件，写入前会先试解析并留一份 `.bak`。

## 🔗 与 [angel_brush](https://github.com/kawayiYokami/astrbot_plugin_angel_brush) 的关系

`providers.json` 的字段格式兼容 [angel_brush](https://github.com/kawayiYokami/astrbot_plugin_angel_brush)，方便沿用已有的提供方与密钥；实现代码是独立编写的，不修改 [angel_brush](https://github.com/kawayiYokami/astrbot_plugin_angel_brush) 的文件。感谢原作者 kawayiYokami 的格式设计。

## 📄 许可

MIT，见 [LICENSE](./LICENSE)。
