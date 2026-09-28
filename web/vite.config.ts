import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// ★产物必须是**扁平文件名**，理由不是洁癖：
//   app.py:35 的 WHITELIST 只认文件名，而 verify_agent.py:348-352 有一条结构性判据
//   把名单里带 `/` 的条目一律判为可疑。Vite 默认吐 `assets/xxx-<hash>.js` ⇒ 既 404
//   又会打红那条安全判据。配成扁平 ⇒ 白名单只加两条扁平条目，**判据强度不下降**。
//
// hash 关掉之后缓存靠查询串（app.py 里用 ?v=<buildId>），不走文件名。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    cssCodeSplit: false, // 样式并进单个 app.css，不按 chunk 拆
    // vite 8 换成了 rolldown，键名同时写两份：命中哪份都不至于静默退回默认命名
    rollupOptions: {
      output: {
        entryFileNames: 'app.js',
        chunkFileNames: 'app.js',
        assetFileNames: 'app.[ext]',
      },
    },
    rolldownOptions: {
      output: {
        entryFileNames: 'app.js',
        chunkFileNames: 'app.js',
        assetFileNames: 'app.[ext]',
      },
    },
  },
})
