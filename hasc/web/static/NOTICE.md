# Third-party assets and sources

Everything under `hasc/web/static/` is served from the Dell. No file is loaded from a CDN at runtime.

| File | Source | Licence |
| --- | --- | --- |
| `img/logo.webp` | HASC VN logo, copied from `frontend/public/logo.webp` in https://github.com/lystiger/HASC (the project's own asset) | Project asset, all rights held by HASC VN / Lystiger |
| `fonts/inter-latin-400.woff2`, `inter-latin-500.woff2`, `inter-latin-700.woff2` | Inter 5.1.1, Latin subset, from `@fontsource/inter@5.1.1` (npm, https://fontsource.org/fonts/inter). Upstream: https://github.com/rsms/inter | SIL Open Font License 1.1 |
| `fonts/jetbrains-mono-latin-400.woff2`, `jetbrains-mono-latin-700.woff2` | JetBrains Mono 5.1.1, Latin subset, from `@fontsource/jetbrains-mono@5.1.1` (npm, https://fontsource.org/fonts/jetbrains-mono). Upstream: https://github.com/JetBrains/JetBrainsMono | SIL Open Font License 1.1 |
| `js/htmx.min.js` | htmx 2.0.4, from `htmx.org@2.0.4` (npm, `dist/htmx.min.js`). Upstream: https://htmx.org | Zero-Clause BSD (0BSD) |

The SIL OFL 1.1 and 0BSD licence texts ship with the upstream packages. To update a pinned asset, fetch the new version from the source above, replace the file, and update this table.

`css/app.css` is written for this project.
