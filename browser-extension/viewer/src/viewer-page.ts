// 展示页的启动脚本。逻辑都在 `page.ts` 里，那边不碰全局，才测得动。
import { fileOf } from "./intercept.ts";
import { load } from "./page.ts";

void load(document, fileOf(location.href));
