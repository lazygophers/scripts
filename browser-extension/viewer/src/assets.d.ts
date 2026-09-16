/**
 * 样式表和字体是打包器的输入，不是 TypeScript 的。
 *
 * `import "katex/dist/katex.min.css"` 这一行是说给 esbuild 听的：把这张表连同它引的字体
 * 一起打进 dist。TypeScript 7 起会去找这类导入的类型声明，找不到就报 TS2882，所以在这里
 * 一次性声明它们没有类型。
 */
declare module "*.css";
declare module "*.woff";
declare module "*.woff2";
declare module "*.ttf";
