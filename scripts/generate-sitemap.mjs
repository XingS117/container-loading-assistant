#!/usr/bin/env node
/**
 * 站点地图 + robots.txt 生成脚本（自托管，0 依赖）
 * 替代 vite-plugin-sitemap（v0.7.1 与 vite 7 不兼容，会抛 "options.forEach is not a function"）
 * 用法：node scripts/generate-sitemap.mjs
 * 触发：已写入根 package.json 的 build 脚本末尾
 */
import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HOST = "https://packing.xingshuwen.com";

// 与 docs/推广方案_装柜助手_v2_纯免费.md 内容矩阵保持一致
// 修改路由后请同步更新 sitemap 与百度站长平台主动推送清单
// ⚠️ 路径一律**带尾斜杠**，必须与各页 <link rel="canonical"> 完全一致，
//    否则 sitemap 里的 URL 会被搜索引擎判为「非规范 URL」，稀释收录权重。
// ⚠️ 新增页面务必同步下表 —— 2026-09-28 曾漏掉 /case/abc-test/ 导致线上 sitemap 少一条。
const ROUTES = [
  { path: "/", changefreq: "weekly", priority: "1.0" },
  { path: "/case/40hq-loading/", changefreq: "monthly", priority: "0.8" },
  { path: "/case/abc-test/", changefreq: "monthly", priority: "0.8" },
  { path: "/case/20gp-loading/", changefreq: "monthly", priority: "0.8" },
  { path: "/tutorial/excel-import/", changefreq: "monthly", priority: "0.8" },
  { path: "/tutorial/weight-balancing/", changefreq: "monthly", priority: "0.8" },
  { path: "/blog/container-loading-algorithm/", changefreq: "monthly", priority: "0.7" },
];

const __dirname = dirname(fileURLToPath(import.meta.url));
// Vite 配置把 root 设为 frontend/，所以 outDir 是 frontend/dist/，而非项目根 dist/
const distDir = resolve(__dirname, "..", "frontend", "dist");

if (!existsSync(distDir)) {
  console.error(`✗ dist/ 不存在，请先执行 pnpm build（期望路径：${distDir}）`);
  process.exit(1);
}

// 用本地（北京）日期，不用 toISOString()——后者是 UTC，北京时间早上跑会退到前一天
const now = new Date();
const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${ROUTES.map(r => `  <url>
    <loc>${HOST}${r.path}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${r.changefreq}</changefreq>
    <priority>${r.priority}</priority>
  </url>`).join("\n")}
</urlset>
`;

const robots = `User-agent: *
Allow: /

Sitemap: ${HOST}/sitemap.xml
`;

writeFileSync(join(distDir, "sitemap.xml"), sitemap, "utf-8");
writeFileSync(join(distDir, "robots.txt"), robots, "utf-8");

console.log(`✓ sitemap.xml generated (${ROUTES.length} routes)`);
console.log(`✓ robots.txt generated`);
