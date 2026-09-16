// 设置页的启动脚本。逻辑都在 `settings.ts` 里，那边不碰全局，才测得动。
import { renderSettings } from "./settings.ts";

void renderSettings(document);
